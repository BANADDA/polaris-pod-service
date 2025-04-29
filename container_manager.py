"""
Container Manager - Main module for container creation and management
"""
import asyncio
import json
import logging
import random
import string
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from paramiko.client import SSHClient

from .gpu_detector import GPUDetector
from .rootless_docker import RootlessDockerSetup

logger = logging.getLogger(__name__)

class ContainerInfo:
    """Container information class"""
    def __init__(self, 
                 container_id: str, 
                 container_name: str, 
                 image: str,
                 ports: Dict[str, str],
                 gpu_enabled: bool = False,
                 gpu_count: int = 0,
                 gpu_type: str = "None",
                 creation_time: float = None,
                 status: str = "created"):
        self.container_id = container_id
        self.container_name = container_name
        self.image = image
        self.ports = ports  # {"container_port": "host_port"}
        self.gpu_enabled = gpu_enabled
        self.gpu_count = gpu_count
        self.gpu_type = gpu_type
        self.creation_time = creation_time or time.time()
        self.status = status
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "container_id": self.container_id,
            "container_name": self.container_name,
            "image": self.image,
            "ports": self.ports,
            "gpu_enabled": self.gpu_enabled,
            "gpu_count": self.gpu_count,
            "gpu_type": self.gpu_type,
            "creation_time": self.creation_time,
            "status": self.status
        }

class ContainerManager:
    """
    Manager for creating and managing Docker containers with support for:
    - GPU detection and passthrough
    - Rootless Docker operation
    - Docker-in-Docker (DinD) support
    - Port mapping and management
    """
    
    def __init__(self):
        self.containers = {}  # container_name -> ContainerInfo
        
    async def create_container(self, 
                              ssh_client: SSHClient,
                              image: str,
                              container_name: Optional[str] = None,
                              ports: Optional[Dict[str, str]] = None,
                              volumes: Optional[Dict[str, str]] = None,
                              environment: Optional[Dict[str, str]] = None,
                              enable_gpu: bool = True,
                              cpu_limit: Optional[str] = None,
                              memory_limit: Optional[str] = None,
                              network: Optional[str] = None,
                              use_sudo: bool = False,
                              dind_enabled: bool = False,
                              rootless: bool = True) -> Optional[ContainerInfo]:
        """
        Create a Docker container with specified configuration.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            image: Docker image to use
            container_name: Name for the container (generated if None)
            ports: Dictionary of port mappings {"container_port": "host_port"}
            volumes: Dictionary of volume mappings {"host_path": "container_path"}
            environment: Dictionary of environment variables
            enable_gpu: Whether to enable GPU if available
            cpu_limit: CPU limit (e.g., "2")
            memory_limit: Memory limit (e.g., "4g")
            network: Docker network to use
            use_sudo: Whether to use sudo if needed
            dind_enabled: Whether to enable Docker-in-Docker
            rootless: Whether to use rootless Docker
            
        Returns:
            ContainerInfo object if successful, None otherwise
        """
        logger.info(f"Creating container with image: {image}")
        
        try:
            # Generate container name if not provided
            if not container_name:
                prefix = "polaris-pod-"
                suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                timestamp = int(time.time())
                container_name = f"{prefix}{timestamp}-{suffix}"
                logger.info(f"Generated container name: {container_name}")
            
            # Make sure rootless Docker is set up if needed
            if rootless:
                logger.info("Setting up rootless Docker if needed")
                rootless_setup = await RootlessDockerSetup.check_rootless_docker(ssh_client)
                if not rootless_setup:
                    logger.info("Rootless Docker not detected, attempting to set up")
                    await RootlessDockerSetup.setup_rootless_docker(ssh_client)
                    
                    # Check again to make sure setup was successful
                    rootless_setup = await RootlessDockerSetup.check_rootless_docker(ssh_client)
                    if not rootless_setup:
                        logger.warning("Rootless Docker setup failed, will try direct Docker")
                        rootless = False
            
            # Check for GPU if enabled
            gpu_info = {"has_gpu": False, "count": 0, "types": [], "has_toolkit": False}
            if enable_gpu:
                logger.info("Checking for GPU support")
                has_gpu, gpu_details = await GPUDetector.detect_nvidia_gpu(ssh_client)
                gpu_info = gpu_details
                
                if has_gpu and gpu_info["has_drivers"] and not gpu_info["has_toolkit"]:
                    logger.info("GPU drivers found but NVIDIA Container Toolkit missing, attempting to install")
                    toolkit_setup = await GPUDetector.setup_nvidia_container_toolkit(ssh_client)
                    
                    if toolkit_setup:
                        logger.info("NVIDIA Container Toolkit installed successfully")
                        gpu_info["has_toolkit"] = True
                    else:
                        logger.warning("Failed to install NVIDIA Container Toolkit, GPU support will be disabled")
                        
                if has_gpu and gpu_info["has_drivers"] and gpu_info["has_toolkit"]:
                    logger.info(f"GPU support enabled: {gpu_info['count']} GPU(s) detected")
                else:
                    logger.info("GPU support disabled: No compatible GPU found or drivers/toolkit missing")
                    enable_gpu = False
            
            # Prepare Docker run command
            cmd_parts = ["docker run -d"]
            
            # Add container name
            cmd_parts.append(f"--name {container_name}")
            
            # Add resource limits
            if cpu_limit:
                cmd_parts.append(f"--cpus={cpu_limit}")
            if memory_limit:
                cmd_parts.append(f"--memory={memory_limit}")
                
            # Add GPU flags if enabled and available
            if enable_gpu and gpu_info["has_gpu"] and gpu_info["has_toolkit"]:
                cmd_parts.append("--gpus all")
            
            # Add network if specified
            if network:
                cmd_parts.append(f"--network {network}")
            
            # Add port mappings
            if ports:
                for container_port, host_port in ports.items():
                    cmd_parts.append(f"-p {host_port}:{container_port}")
            
            # Add volume mappings
            if volumes:
                for host_path, container_path in volumes.items():
                    cmd_parts.append(f"-v {host_path}:{container_path}")
            
            # Add environment variables
            if environment:
                for key, value in environment.items():
                    cmd_parts.append(f"-e {key}={value}")
            
            # Add Docker-in-Docker configuration if enabled
            if dind_enabled:
                # Add privileged mode for Docker-in-Docker
                cmd_parts.append("--privileged")
                
                # Add Docker socket volume
                cmd_parts.append("-v /var/run/docker.sock:/var/run/docker.sock")
                
                # Add additional volumes needed for Docker-in-Docker
                cmd_parts.append("-v /usr/bin/docker:/usr/bin/docker")
                
                # Add environment variables
                cmd_parts.append("-e DOCKER_TLS_CERTDIR=")
            
            # Add the image
            cmd_parts.append(image)
            
            # Construct the final command
            docker_run_cmd = " ".join(cmd_parts)
            logger.info(f"Docker run command: {docker_run_cmd}")
            
            # Run the command
            if rootless:
                exit_status, output, error = await RootlessDockerSetup.run_docker_command(
                    ssh_client, 
                    docker_run_cmd.replace("docker run", "run"), # Remove "docker" prefix since it's added in the method
                    use_sudo=use_sudo
                )
            else:
                # Use sudo if needed
                if use_sudo:
                    docker_run_cmd = f"sudo {docker_run_cmd}"
                
                stdin, stdout, stderr = ssh_client.exec_command(docker_run_cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
            
            if exit_status != 0:
                logger.error(f"Error creating container: {error}")
                return None
            
            # Get container ID from output
            container_id = output.strip()
            logger.info(f"Container created: {container_id}")
            
            # Verify container is running
            await asyncio.sleep(2)  # Give it a moment to start
            
            if rootless:
                exit_status, inspect_output, error = await RootlessDockerSetup.run_docker_command(
                    ssh_client,
                    f"inspect {container_id}",
                    use_sudo=use_sudo
                )
            else:
                inspect_cmd = f"docker inspect {container_id}"
                if use_sudo:
                    inspect_cmd = f"sudo {inspect_cmd}"
                    
                stdin, stdout, stderr = ssh_client.exec_command(inspect_cmd)
                exit_status = stdout.channel.recv_exit_status()
                inspect_output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
            
            if exit_status != 0:
                logger.error(f"Error inspecting container: {error}")
                return None
            
            # Parse container info
            container_info = None
            try:
                inspect_data = json.loads(inspect_output)
                if not inspect_data or not isinstance(inspect_data, list) or len(inspect_data) == 0:
                    logger.error("Invalid container inspect data")
                    return None
                
                # Extract container status
                status = inspect_data[0].get("State", {}).get("Status", "unknown")
                
                # Extract port mappings
                port_mappings = {}
                ports_info = inspect_data[0].get("NetworkSettings", {}).get("Ports", {})
                for container_port, host_bindings in ports_info.items():
                    if host_bindings and isinstance(host_bindings, list) and len(host_bindings) > 0:
                        host_port = host_bindings[0].get("HostPort", "")
                        if host_port:
                            # Extract just the port number from container_port (e.g., "80/tcp" -> "80")
                            container_port_num = container_port.split("/")[0]
                            port_mappings[container_port_num] = host_port
                
                # Create container info object
                container_info = ContainerInfo(
                    container_id=container_id,
                    container_name=container_name,
                    image=image,
                    ports=port_mappings,
                    gpu_enabled=enable_gpu and gpu_info["has_gpu"] and gpu_info["has_toolkit"],
                    gpu_count=gpu_info["count"] if enable_gpu and gpu_info["has_gpu"] else 0,
                    gpu_type=gpu_info["types"][0] if enable_gpu and gpu_info["has_gpu"] and gpu_info["types"] else "None",
                    creation_time=time.time(),
                    status=status
                )
                
                self.containers[container_name] = container_info
                logger.info(f"Container info: {container_info.to_dict()}")
                
            except json.JSONDecodeError:
                logger.error(f"Failed to parse container inspect data: {inspect_output}")
                return None
            except Exception as e:
                logger.error(f"Error processing container info: {str(e)}", exc_info=True)
                return None
            
            return container_info
            
        except Exception as e:
            logger.error(f"Error creating container: {str(e)}", exc_info=True)
            return None
    
    async def setup_pod_user(self, 
                            ssh_client: SSHClient, 
                            container_id: str, 
                            username: str = "pod-user", 
                            password: Optional[str] = None,
                            use_sudo: bool = False,
                            rootless: bool = True) -> bool:
        """
        Set up a non-root user inside the container to avoid sudo password issues.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            container_id: Container ID or name
            username: Username to create
            password: Password for the user (generated if None)
            use_sudo: Whether to use sudo if needed
            rootless: Whether to use rootless Docker
            
        Returns:
            Boolean indicating success
        """
        logger.info(f"Setting up {username} in container {container_id}")
        
        try:
            # Generate random password if not provided
            if not password:
                password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
                logger.info(f"Generated random password for {username}")
            
            # Commands to set up the user
            setup_commands = [
                # Update package lists first
                "apt-get update",
                # Install base dependencies including sudo and mosh
                f"apt-get install -y --no-install-recommends sudo mosh",
                # Create user and add to sudoers
                f"useradd -m -s /bin/bash {username}",
                f"echo '{username} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/{username}",
                f"chmod 0440 /etc/sudoers.d/{username}",
                # Set password
                f"echo '{username}:{password}' | chpasswd",
                # Create .ssh directory
                f"mkdir -p /home/{username}/.ssh",
                f"chown -R {username}:{username} /home/{username}/.ssh",
                f"chmod 700 /home/{username}/.ssh",
            ]
            
            # Add Docker setup (for DinD)
            docker_setup_commands = [
                # Install Docker if not already installed
                "which docker || curl -fsSL https://get.docker.com | bash",
                # Allow user to use Docker
                f"usermod -aG docker {username}",
                # Setup rootless Docker for the user
                f"runuser -l {username} -c 'dockerd-rootless-setuptool.sh install'"
            ]
            
            # Combine all commands into a single script
            setup_script = " && ".join(setup_commands)
            
            # Add Docker setup commands if needed
            docker_script = " && ".join(docker_setup_commands)
            
            # Run user setup commands in container
            logger.info("Running user setup commands in container")
            user_exec_cmd = f"docker exec {container_id} bash -c '{setup_script}'"
            
            if rootless:
                exit_status, output, error = await RootlessDockerSetup.run_docker_command(
                    ssh_client,
                    f"exec {container_id} bash -c '{setup_script}'",
                    use_sudo=use_sudo
                )
            else:
                # Use sudo if needed
                if use_sudo:
                    user_exec_cmd = f"sudo {user_exec_cmd}"
                
                stdin, stdout, stderr = ssh_client.exec_command(user_exec_cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
            
            if exit_status != 0:
                logger.error(f"Error setting up user in container: {error}")
                return False
            
            # Run Docker setup commands if needed (separately to isolate potential failures)
            logger.info("Running Docker setup commands in container")
            docker_exec_cmd = f"docker exec {container_id} bash -c '{docker_script}'"
            
            if rootless:
                exit_status, output, error = await RootlessDockerSetup.run_docker_command(
                    ssh_client,
                    f"exec {container_id} bash -c '{docker_script}'",
                    use_sudo=use_sudo
                )
            else:
                # Use sudo if needed
                if use_sudo:
                    docker_exec_cmd = f"sudo {docker_exec_cmd}"
                
                stdin, stdout, stderr = ssh_client.exec_command(docker_exec_cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
            
            # Docker setup might fail if Docker is not available, but we still consider user setup successful
            if exit_status != 0:
                logger.warning(f"Docker setup in container may have failed: {error}")
            
            logger.info(f"User {username} set up successfully in container {container_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error setting up user in container: {str(e)}", exc_info=True)
            return False
    
    async def check_container_status(self, 
                                    ssh_client: SSHClient, 
                                    container_id: str,
                                    use_sudo: bool = False,
                                    rootless: bool = True) -> Tuple[bool, str]:
        """
        Check if a container is running.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            container_id: Container ID or name
            use_sudo: Whether to use sudo if needed
            rootless: Whether to use rootless Docker
            
        Returns:
            Tuple of (is_running, status)
        """
        logger.info(f"Checking status of container {container_id}")
        
        try:
            # Check if container is running
            if rootless:
                exit_status, output, error = await RootlessDockerSetup.run_docker_command(
                    ssh_client,
                    f"inspect --format={{{{.State.Status}}}} {container_id}",
                    use_sudo=use_sudo
                )
            else:
                inspect_cmd = f"docker inspect --format={{{{.State.Status}}}} {container_id}"
                if use_sudo:
                    inspect_cmd = f"sudo {inspect_cmd}"
                    
                stdin, stdout, stderr = ssh_client.exec_command(inspect_cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
            
            if exit_status != 0:
                logger.error(f"Error checking container status: {error}")
                return False, "error"
            
            status = output.strip()
            logger.info(f"Container status: {status}")
            
            return status == "running", status
            
        except Exception as e:
            logger.error(f"Error checking container status: {str(e)}", exc_info=True)
            return False, "error"
    
    async def stop_container(self, 
                           ssh_client: SSHClient, 
                           container_id: str,
                           use_sudo: bool = False,
                           rootless: bool = True) -> bool:
        """
        Stop a container.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            container_id: Container ID or name
            use_sudo: Whether to use sudo if needed
            rootless: Whether to use rootless Docker
            
        Returns:
            Boolean indicating success
        """
        logger.info(f"Stopping container {container_id}")
        
        try:
            # Stop container
            if rootless:
                exit_status, output, error = await RootlessDockerSetup.run_docker_command(
                    ssh_client,
                    f"stop {container_id}",
                    use_sudo=use_sudo
                )
            else:
                stop_cmd = f"docker stop {container_id}"
                if use_sudo:
                    stop_cmd = f"sudo {stop_cmd}"
                    
                stdin, stdout, stderr = ssh_client.exec_command(stop_cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
            
            if exit_status != 0:
                logger.error(f"Error stopping container: {error}")
                return False
            
            logger.info(f"Container stopped: {output}")
            
            # Update container info if we have it
            if container_id in self.containers:
                self.containers[container_id].status = "stopped"
            
            return True
            
        except Exception as e:
            logger.error(f"Error stopping container: {str(e)}", exc_info=True)
            return False
    
    async def remove_container(self, 
                             ssh_client: SSHClient, 
                             container_id: str,
                             force: bool = False,
                             use_sudo: bool = False,
                             rootless: bool = True) -> bool:
        """
        Remove a container.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            container_id: Container ID or name
            force: Whether to force removal
            use_sudo: Whether to use sudo if needed
            rootless: Whether to use rootless Docker
            
        Returns:
            Boolean indicating success
        """
        logger.info(f"Removing container {container_id} (force={force})")
        
        try:
            # Remove container
            cmd = f"rm {'-f' if force else ''} {container_id}"
            
            if rootless:
                exit_status, output, error = await RootlessDockerSetup.run_docker_command(
                    ssh_client,
                    cmd,
                    use_sudo=use_sudo
                )
            else:
                rm_cmd = f"docker {cmd}"
                if use_sudo:
                    rm_cmd = f"sudo {rm_cmd}"
                    
                stdin, stdout, stderr = ssh_client.exec_command(rm_cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
            
            if exit_status != 0:
                logger.error(f"Error removing container: {error}")
                return False
            
            logger.info(f"Container removed: {output}")
            
            # Remove container info
            if container_id in self.containers:
                del self.containers[container_id]
            
            return True
            
        except Exception as e:
            logger.error(f"Error removing container: {str(e)}", exc_info=True)
            return False 