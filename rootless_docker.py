"""
Rootless Docker Setup Module - Handles setup of Docker in a rootless configuration
"""
import asyncio
import logging
import os
from typing import Dict, List, Optional, Tuple, Union

from paramiko.client import SSHClient

logger = logging.getLogger(__name__)

class RootlessDockerSetup:
    """Setup and manage rootless Docker instances."""
    
    @staticmethod
    async def check_rootless_docker(ssh_client: SSHClient) -> bool:
        """
        Check if rootless Docker is already configured.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            
        Returns:
            Boolean indicating if rootless Docker is configured
        """
        logger.info("Checking if rootless Docker is already configured")
        try:
            # Check if docker socket exists in XDG_RUNTIME_DIR
            check_cmd = "ls -la ${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock 2>/dev/null || echo 'not-found'"
            stdin, stdout, stderr = ssh_client.exec_command(check_cmd)
            output = stdout.read().decode().strip()
            has_rootless = "not-found" not in output
            
            if has_rootless:
                logger.info("Rootless Docker socket found")
                
                # Verify if the daemon is running
                check_daemon_cmd = "ps aux | grep -v grep | grep -i 'dockerd.*rootless' || echo 'not-running'"
                stdin, stdout, stderr = ssh_client.exec_command(check_daemon_cmd)
                daemon_output = stdout.read().decode().strip()
                daemon_running = "not-running" not in daemon_output
                
                if daemon_running:
                    logger.info("Rootless Docker daemon is running")
                    return True
                else:
                    logger.warning("Rootless Docker socket found but daemon is not running")
                    return False
            else:
                logger.info("Rootless Docker is not configured")
                return False
                
        except Exception as e:
            logger.error(f"Error checking rootless Docker: {str(e)}", exc_info=True)
            return False
    
    @staticmethod
    async def setup_rootless_docker(ssh_client: SSHClient, username: str = None) -> bool:
        """
        Set up rootless Docker on the target system.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            username: Optional username to set up rootless Docker for (uses current user if None)
            
        Returns:
            Boolean indicating if setup was successful
        """
        logger.info(f"Setting up rootless Docker{f' for user {username}' if username else ''}")
        
        try:
            # Get current user if not provided
            if not username:
                stdin, stdout, stderr = ssh_client.exec_command("whoami")
                username = stdout.read().decode().strip()
                logger.info(f"Using current user: {username}")
            
            # Check if docker-ce is installed
            check_docker_cmd = "command -v docker >/dev/null 2>&1 && echo 'found' || echo 'not-found'"
            stdin, stdout, stderr = ssh_client.exec_command(check_docker_cmd)
            docker_installed = stdout.read().decode().strip() == 'found'
            
            if not docker_installed:
                logger.warning("Docker CLI not found, need to install Docker first")
                # Install Docker CE
                await RootlessDockerSetup._install_docker_ce(ssh_client)
                
            # Check if rootless extras are installed
            check_extras_cmd = "command -v dockerd-rootless.sh >/dev/null 2>&1 && echo 'found' || echo 'not-found'"
            stdin, stdout, stderr = ssh_client.exec_command(check_extras_cmd)
            extras_installed = stdout.read().decode().strip() == 'found'
            
            if not extras_installed:
                logger.info("Installing docker-rootless-extras")
                await RootlessDockerSetup._install_rootless_extras(ssh_client)
            
            # Configure user namespace
            logger.info("Configuring user namespace")
            await RootlessDockerSetup._configure_user_namespace(ssh_client, username)
            
            # Check/modify .bashrc or .profile to set environment variables
            logger.info("Setting up environment variables")
            env_setup_commands = [
                f"echo 'export PATH=/usr/bin:$PATH' >> ~/.bashrc",
                f"echo 'export DOCKER_HOST=unix://${{XDG_RUNTIME_DIR}}/docker.sock' >> ~/.bashrc",
                f"echo 'export DOCKER_HOST=unix://${{XDG_RUNTIME_DIR}}/docker.sock' >> ~/.profile",
            ]
            
            for cmd in env_setup_commands:
                stdin, stdout, stderr = ssh_client.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                
            # Start rootless Docker daemon if not running
            logger.info("Starting rootless Docker daemon")
            daemon_commands = [
                "systemctl --user enable docker",
                "systemctl --user start docker",
                "systemctl --user status docker || echo 'service not started'"
            ]
            
            # First try user service method
            success = False
            for cmd in daemon_commands:
                stdin, stdout, stderr = ssh_client.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
                
                if "service not started" not in output and "could not be found" not in error:
                    success = True
            
            # If service method failed, try direct method
            if not success:
                logger.info("User service method failed, trying direct dockerd-rootless.sh")
                direct_cmd = "dockerd-rootless.sh >/dev/null 2>&1 & echo $!"
                stdin, stdout, stderr = ssh_client.exec_command(direct_cmd)
                pid = stdout.read().decode().strip()
                
                if pid:
                    logger.info(f"Started rootless Docker daemon with PID {pid}")
                    success = True
                else:
                    logger.warning("Failed to start rootless Docker daemon")
                    success = False
            
            # Verify Docker is working
            if success:
                verify_cmd = f"DOCKER_HOST=unix://${{XDG_RUNTIME_DIR}}/docker.sock docker info 2>/dev/null || echo 'verification failed'"
                stdin, stdout, stderr = ssh_client.exec_command(verify_cmd)
                output = stdout.read().decode().strip()
                
                if "verification failed" not in output:
                    logger.info("Rootless Docker setup verification successful")
                    return True
                else:
                    logger.error("Rootless Docker verification failed")
                    error = stderr.read().decode().strip()
                    logger.error(f"Docker info error: {error}")
                    return False
            else:
                return False
                
        except Exception as e:
            logger.error(f"Error setting up rootless Docker: {str(e)}", exc_info=True)
            return False
    
    @staticmethod
    async def _install_docker_ce(ssh_client: SSHClient) -> bool:
        """Install Docker CE if not already installed."""
        logger.info("Installing Docker CE")
        
        try:
            # Detect distribution
            distro_cmd = "cat /etc/os-release | grep -i '^ID=' | cut -d= -f2"
            stdin, stdout, stderr = ssh_client.exec_command(distro_cmd)
            distro = stdout.read().decode().strip().lower().replace('"', '')
            
            logger.info(f"Detected Linux distribution: {distro}")
            
            if distro in ['ubuntu', 'debian']:
                commands = [
                    "apt-get update",
                    "apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release",
                    "curl -fsSL https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]')/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg",
                    "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]') $(lsb_release -cs) stable\" | tee /etc/apt/sources.list.d/docker.list > /dev/null",
                    "apt-get update",
                    "apt-get install -y docker-ce docker-ce-cli containerd.io"
                ]
            elif distro in ['centos', 'rhel', 'fedora', 'rocky', 'almalinux']:
                commands = [
                    "dnf -y install dnf-plugins-core",
                    "dnf config-manager --add-repo https://download.docker.com/linux/$(cat /etc/redhat-release | awk '{print tolower($1)}')/docker-ce.repo",
                    "dnf install -y docker-ce docker-ce-cli containerd.io"
                ]
            else:
                logger.warning(f"Unsupported distribution: {distro}, cannot install Docker CE automatically")
                return False
                
            # Use sudo if available
            stdin, stdout, stderr = ssh_client.exec_command("command -v sudo && echo 'sudo available' || echo 'no sudo'")
            has_sudo = "sudo available" in stdout.read().decode().strip()
            
            for i, cmd in enumerate(commands):
                if has_sudo:
                    cmd = f"sudo {cmd}"
                
                logger.info(f"Running install command ({i+1}/{len(commands)}): {cmd}")
                stdin, stdout, stderr = ssh_client.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                
                if exit_status != 0:
                    error = stderr.read().decode().strip()
                    logger.error(f"Error running command: {error}")
                    return False
            
            # Verify Docker was installed
            verify_cmd = "command -v docker >/dev/null 2>&1 && echo 'found' || echo 'not-found'"
            stdin, stdout, stderr = ssh_client.exec_command(verify_cmd)
            installed = stdout.read().decode().strip() == 'found'
            
            if installed:
                logger.info("Docker CE installed successfully")
                return True
            else:
                logger.error("Docker CE installation verification failed")
                return False
                
        except Exception as e:
            logger.error(f"Error installing Docker CE: {str(e)}", exc_info=True)
            return False
    
    @staticmethod
    async def _install_rootless_extras(ssh_client: SSHClient) -> bool:
        """Install docker-rootless-extras package."""
        logger.info("Installing docker-rootless-extras")
        
        try:
            # Detect distribution
            distro_cmd = "cat /etc/os-release | grep -i '^ID=' | cut -d= -f2"
            stdin, stdout, stderr = ssh_client.exec_command(distro_cmd)
            distro = stdout.read().decode().strip().lower().replace('"', '')
            
            logger.info(f"Detected Linux distribution: {distro}")
            
            if distro in ['ubuntu', 'debian']:
                cmd = "apt-get install -y docker-ce-rootless-extras uidmap slirp4netns fuse-overlayfs"
            elif distro in ['centos', 'rhel', 'fedora', 'rocky', 'almalinux']:
                cmd = "dnf install -y docker-ce-rootless-extras fuse-overlayfs slirp4netns"
            else:
                logger.warning(f"Unsupported distribution: {distro}")
                return False
                
            # Use sudo if available
            stdin, stdout, stderr = ssh_client.exec_command("command -v sudo && echo 'sudo available' || echo 'no sudo'")
            if "sudo available" in stdout.read().decode().strip():
                cmd = f"sudo {cmd}"
            
            logger.info(f"Running: {cmd}")
            stdin, stdout, stderr = ssh_client.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            
            if exit_status != 0:
                error = stderr.read().decode().strip()
                logger.error(f"Error installing rootless extras: {error}")
                return False
            
            # Verify Docker rootless extras were installed
            verify_cmd = "command -v dockerd-rootless.sh >/dev/null 2>&1 && echo 'found' || echo 'not-found'"
            stdin, stdout, stderr = ssh_client.exec_command(verify_cmd)
            installed = stdout.read().decode().strip() == 'found'
            
            if installed:
                logger.info("Docker rootless extras installed successfully")
                return True
            else:
                logger.error("Docker rootless extras installation verification failed")
                return False
                
        except Exception as e:
            logger.error(f"Error installing Docker rootless extras: {str(e)}", exc_info=True)
            return False
    
    @staticmethod
    async def _configure_user_namespace(ssh_client: SSHClient, username: str) -> bool:
        """Configure user namespace capabilities."""
        logger.info(f"Configuring user namespace for {username}")
        
        try:
            # Check if unprivileged_userns_clone is enabled
            check_cmd = "sysctl -a 2>/dev/null | grep -E 'kernel\\.unprivileged_userns_clone' || echo 'not-found'"
            stdin, stdout, stderr = ssh_client.exec_command(check_cmd)
            output = stdout.read().decode().strip()
            
            if "not-found" not in output:
                userns_enabled = "1" in output
                
                if not userns_enabled:
                    logger.info("Enabling unprivileged_userns_clone")
                    enable_cmd = "sudo sysctl -w kernel.unprivileged_userns_clone=1"
                    stdin, stdout, stderr = ssh_client.exec_command(enable_cmd)
                    exit_status = stdout.channel.recv_exit_status()
                    
                    if exit_status != 0:
                        logger.warning("Failed to enable unprivileged_userns_clone")
            else:
                logger.info("unprivileged_userns_clone sysctl not found, skipping")
            
            # Configure /etc/subuid and /etc/subgid
            uid_cmd = f"id -u {username}"
            stdin, stdout, stderr = ssh_client.exec_command(uid_cmd)
            uid = stdout.read().decode().strip()
            
            if not uid:
                logger.error(f"Could not determine UID for user {username}")
                return False
                
            # Check if user already has subuid/subgid entries
            check_subuid_cmd = f"grep -E '^{username}:' /etc/subuid || echo 'not-found'"
            stdin, stdout, stderr = ssh_client.exec_command(check_subuid_cmd)
            has_subuid = "not-found" not in stdout.read().decode().strip()
            
            check_subgid_cmd = f"grep -E '^{username}:' /etc/subgid || echo 'not-found'"
            stdin, stdout, stderr = ssh_client.exec_command(check_subgid_cmd)
            has_subgid = "not-found" not in stdout.read().decode().strip()
            
            if not has_subuid or not has_subgid:
                logger.info(f"Adding subuid/subgid entries for {username}")
                
                # Generate commands with sudo if needed
                add_subuid_cmd = f"echo '{username}:100000:65536' | sudo tee -a /etc/subuid > /dev/null"
                add_subgid_cmd = f"echo '{username}:100000:65536' | sudo tee -a /etc/subgid > /dev/null"
                
                if not has_subuid:
                    stdin, stdout, stderr = ssh_client.exec_command(add_subuid_cmd)
                    exit_status = stdout.channel.recv_exit_status()
                    
                    if exit_status != 0:
                        logger.warning(f"Failed to add subuid entry for {username}")
                        
                if not has_subgid:
                    stdin, stdout, stderr = ssh_client.exec_command(add_subgid_cmd)
                    exit_status = stdout.channel.recv_exit_status()
                    
                    if exit_status != 0:
                        logger.warning(f"Failed to add subgid entry for {username}")
            
            return True
                
        except Exception as e:
            logger.error(f"Error configuring user namespace: {str(e)}", exc_info=True)
            return False
            
    @staticmethod
    async def run_docker_command(ssh_client: SSHClient, command: str, use_sudo: bool = False) -> Tuple[int, str, str]:
        """
        Run a Docker command using the rootless Docker socket if available.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            command: Docker command to run (without 'docker' prefix)
            use_sudo: Whether to try using sudo if rootless fails
            
        Returns:
            Tuple of (exit_status, stdout, stderr)
        """
        logger.info(f"Running Docker command: {command}")
        
        try:
            # Check if rootless Docker is configured
            has_rootless = await RootlessDockerSetup.check_rootless_docker(ssh_client)
            
            if has_rootless:
                # Run with rootless Docker environment
                full_cmd = f"DOCKER_HOST=unix://${{XDG_RUNTIME_DIR}}/docker.sock docker {command}"
                logger.info(f"Running as rootless: {full_cmd}")
                stdin, stdout, stderr = ssh_client.exec_command(full_cmd)
                
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
                
                if exit_status == 0 or 'permission denied' not in error.lower():
                    logger.info("Rootless Docker command completed")
                    return exit_status, output, error
            
            # Fallback to sudo if rootless failed or not configured
            if use_sudo:
                logger.info("Falling back to sudo for Docker command")
                full_cmd = f"sudo docker {command}"
                stdin, stdout, stderr = ssh_client.exec_command(full_cmd)
                
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
                
                return exit_status, output, error
            else:
                logger.warning("Rootless Docker command failed and sudo fallback not enabled")
                return 1, "", "Rootless Docker command failed and sudo fallback not enabled"
                
        except Exception as e:
            logger.error(f"Error running Docker command: {str(e)}", exc_info=True)
            return 1, "", str(e) 