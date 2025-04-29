"""
GPU Detection Module - Detects availability of GPUs on the host system
"""
import asyncio
import logging
import shlex
from typing import Dict, List, Optional, Tuple, Union

from paramiko.client import SSHClient

logger = logging.getLogger(__name__)

# Helper function for local command execution
async def _run_local_command(command: str) -> Tuple[int, str, str]:
    """Runs a command locally using asyncio.create_subprocess_shell."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode().strip()
        stderr = stderr_bytes.decode().strip()
        exit_status = proc.returncode if proc.returncode is not None else 1
        # logger.debug(f"Local command: '{command}' Exit: {exit_status} Stdout: '{stdout[:100]}...' Stderr: '{stderr[:100]}...'")
        return exit_status, stdout, stderr
    except Exception as e:
        logger.error(f"Error running local command '{command}': {e}", exc_info=True)
        return 1, "", str(e)

async def _run_command(
    ssh_client: Optional[SSHClient], 
    command: str
) -> Tuple[int, str, str]:
    """Runs a command either locally or remotely via SSH."""
    if ssh_client:
        try:
            stdin, stdout, stderr = ssh_client.exec_command(command, timeout=30) # Add timeout
            exit_status = stdout.channel.recv_exit_status()
            stdout_output = stdout.read().decode().strip()
            stderr_output = stderr.read().decode().strip()
            # logger.debug(f"SSH command: '{command}' Exit: {exit_status} Stdout: '{stdout_output[:100]}...' Stderr: '{stderr_output[:100]}...'")
            return exit_status, stdout_output, stderr_output
        except Exception as e:
            logger.error(f"Error running SSH command '{command}': {e}", exc_info=True)
            return 1, "", str(e)
    else:
        # Local execution
        return await _run_local_command(command)

class GPUDetector:
    """Detect GPUs on the target system and return their details."""
    
    @staticmethod
    async def detect_nvidia_gpu(ssh_client: Optional[SSHClient]) -> Tuple[bool, Dict]:
        """
        Detect if NVIDIA GPUs are available on the system.
        
        Args:
            ssh_client: Connected Paramiko SSH client or None for local execution.
            
        Returns:
            Tuple of (has_gpu, gpu_info)
                has_gpu: Boolean indicating if NVIDIA GPU is available
                gpu_info: Dictionary with GPU details (count, types, memory, etc.)
        """
        # Determine execution context
        context = "SSH" if ssh_client else "Local"
        logger.info(f"[{context}] Checking for NVIDIA GPU and drivers")
        
        gpu_info = {
            "has_gpu": False,
            "has_drivers": False,
            "has_toolkit": False,
            "count": 0,
            "types": [],
            "memory": [],
            "driver_version": None,
            "cuda_version": None,
        }
        
        try:
            # First check if NVIDIA GPU hardware is detected via lspci
            has_gpu_cmd = "lspci | grep -i nvidia || echo 'No NVIDIA GPU found'"
            exit_status, has_gpu_output, error = await _run_command(ssh_client, has_gpu_cmd)
            gpu_info["has_gpu"] = "No NVIDIA GPU found" not in has_gpu_output and exit_status == 0
            
            if not gpu_info["has_gpu"]:
                logger.info(f"[{context}] No NVIDIA GPU hardware detected")
                return False, gpu_info
                
            logger.info(f"[{context}] NVIDIA GPU hardware detected: {has_gpu_output}")
            
            # Check if nvidia-smi is available (drivers installed)
            driver_cmd = "nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null || echo 'No NVIDIA drivers'"
            exit_status, driver_output, error = await _run_command(ssh_client, driver_cmd)
            gpu_info["has_drivers"] = "No NVIDIA drivers" not in driver_output and exit_status == 0
            
            if not gpu_info["has_drivers"]:
                logger.warning(f"[{context}] NVIDIA GPU hardware detected but drivers not installed")
                return True, gpu_info  # GPU exists but no drivers
                
            # Get driver version
            gpu_info["driver_version"] = driver_output
            logger.info(f"[{context}] NVIDIA drivers detected, version: {driver_output}")
            
            # Get CUDA version
            cuda_cmd = "nvidia-smi --query-gpu=cuda_version --format=csv,noheader 2>/dev/null || echo 'No CUDA'"
            exit_status, cuda_output, error = await _run_command(ssh_client, cuda_cmd)
            if "No CUDA" not in cuda_output and exit_status == 0:
                gpu_info["cuda_version"] = cuda_output
                logger.info(f"[{context}] CUDA version: {cuda_output}")
            
            # Get GPU count
            count_cmd = "nvidia-smi --query-gpu=name --format=csv,noheader | wc -l"
            exit_status, count_output, error = await _run_command(ssh_client, count_cmd)
            if exit_status == 0:
                try:
                    gpu_info["count"] = int(count_output)
                    logger.info(f"[{context}] Number of NVIDIA GPUs: {count_output}")
                except ValueError:
                    gpu_info["count"] = 1  # Assume at least one if parsing fails
                    logger.warning(f"[{context}] Could not parse GPU count, assuming 1. Output: {count_output}")
            else:
                 logger.warning(f"[{context}] Could not determine GPU count. Error: {error}")
            
            # Get GPU types
            types_cmd = "nvidia-smi --query-gpu=name --format=csv,noheader"
            exit_status, types_output_str, error = await _run_command(ssh_client, types_cmd)
            if exit_status == 0:
                types_output = types_output_str.strip().split('\n')
                gpu_info["types"] = types_output
                logger.info(f"[{context}] GPU types: {types_output}")
            else:
                logger.warning(f"[{context}] Could not determine GPU types. Error: {error}")

            # Get GPU memory
            memory_cmd = "nvidia-smi --query-gpu=memory.total --format=csv,noheader"
            exit_status, memory_output_str, error = await _run_command(ssh_client, memory_cmd)
            if exit_status == 0:
                memory_output = memory_output_str.strip().split('\n')
                gpu_info["memory"] = memory_output
                logger.info(f"[{context}] GPU memory: {memory_output}")
            else:
                logger.warning(f"[{context}] Could not determine GPU memory. Error: {error}")

            # Check for nvidia-container-toolkit (required for GPU in Docker)
            # Note: This depends on 'docker' command being available and configured
            # We might need RootlessDockerSetup logic here later if running rootless locally
            toolkit_cmd = "docker info 2>/dev/null | grep -i 'Runtimes:.* nvidia' || echo 'No NVIDIA Docker support'"
            exit_status, toolkit_output, error = await _run_command(ssh_client, toolkit_cmd)
            # Check both exit status and output, as grep returns 1 if not found
            gpu_info["has_toolkit"] = "No NVIDIA Docker support" not in toolkit_output

            if gpu_info["has_toolkit"]:
                logger.info(f"[{context}] NVIDIA Container Toolkit runtime is configured in Docker")
            else:
                logger.warning(f"[{context}] NVIDIA Container Toolkit runtime not found in Docker info")
            
            return True, gpu_info # Return True for has_gpu as hardware was detected initially
            
        except Exception as e:
            logger.error(f"[{context}] Error detecting NVIDIA GPU: {str(e)}", exc_info=True)
            # Return potentially partial info gathered so far
            return gpu_info["has_gpu"], gpu_info
    
    @staticmethod
    async def check_docker_gpu_support(ssh_client: Optional[SSHClient]) -> bool:
        """
        Check if Docker has NVIDIA GPU support configured via nvidia runtime.
        
        Args:
            ssh_client: Connected Paramiko SSH client or None for local execution.
            
        Returns:
            Boolean indicating if Docker has GPU support
        """
        context = "SSH" if ssh_client else "Local"
        logger.info(f"[{context}] Checking Docker NVIDIA runtime support")
        try:
            # Check Docker GPU support via runtime check
            cmd = "docker info 2>/dev/null | grep -i 'Runtimes:.* nvidia' || echo 'No NVIDIA Docker support'"
            exit_status, output, error = await _run_command(ssh_client, cmd)
            has_support = "No NVIDIA Docker support" not in output
            
            if has_support:
                logger.info(f"[{context}] Docker has NVIDIA GPU runtime support")
            else:
                logger.warning(f"[{context}] Docker does not have NVIDIA GPU runtime support")
                
            return has_support
        except Exception as e:
            logger.error(f"[{context}] Error checking Docker GPU support: {str(e)}", exc_info=True)
            return False
            
    @staticmethod
    async def setup_nvidia_container_toolkit(ssh_client: Optional[SSHClient]) -> bool:
        """
        Attempt to set up NVIDIA Container Toolkit.
        
        Args:
            ssh_client: Connected Paramiko SSH client or None for local execution.
            
        Returns:
            Boolean indicating if setup was successful
        """
        context = "SSH" if ssh_client else "Local"
        logger.info(f"[{context}] Setting up NVIDIA Container Toolkit")
        
        # Helper to run setup commands with sudo if needed
        async def run_setup_command(cmd: str, check_sudo: bool = True) -> Tuple[int, str, str]:
            target_cmd = cmd # Command to be executed
            use_sudo = False
            if check_sudo:
                 # Check if sudo is available and needed (only check locally if needed)
                if not ssh_client: 
                    exit_status_whoami, user, _ = await _run_local_command("whoami")
                    is_root = exit_status_whoami == 0 and user == "root"
                    
                    exit_status_sudo, _, _ = await _run_local_command("command -v sudo")
                    has_sudo_cmd = exit_status_sudo == 0
                    
                    if not is_root and has_sudo_cmd:
                         use_sudo = True
                else:
                    # Original SSH sudo check
                    sudo_check_cmd = "command -v sudo && echo 'sudo available' || echo 'no sudo'"
                    exit_status_sudo, output_sudo, _ = await _run_command(ssh_client, sudo_check_cmd)
                    if exit_status_sudo == 0 and "sudo available" in output_sudo:
                        use_sudo = True

            if use_sudo:
                # For commands that modify files directly (sed -i) or use redirection (>)
                # avoid wrapping in `bash -c` as it complicates quoting and permissions.
                # Run them directly with sudo if possible.
                # Note: This assumes the user running the script has passwordless sudo access
                # for these specific commands, or sudo is configured system-wide.
                if "sed -i" in cmd or " > " in cmd or " | tee " in cmd or "mv /tmp/" in cmd:
                    target_cmd = f"sudo {cmd}"
                else:
                    # For other commands, wrap in bash -c for potentially complex chains
                    escaped_cmd = cmd.replace("'", "'\\\\''") 
                    target_cmd = f"sudo bash -c '{escaped_cmd}'"
                
            return await _run_command(ssh_client, target_cmd)

        try:
            # First check for distribution
            distro_cmd = "cat /etc/os-release | grep -E '^ID=' | cut -d= -f2"
            exit_status, distro_raw, error = await _run_command(ssh_client, distro_cmd)
            if exit_status != 0:
                 logger.error(f"[{context}] Could not determine Linux distribution: {error}")
                 return False
                 
            distro = distro_raw.strip().lower().replace('"', '')
            logger.info(f"[{context}] Detected Linux distribution: {distro}")
            
            setup_commands = []
            
            if distro in ['ubuntu', 'debian']:
                setup_commands = [
                    "export DEBIAN_FRONTEND=noninteractive",
                    "apt-get update",
                    "apt-get install -y curl ca-certificates gnupg",
                    # Add --yes to gpg to overwrite without prompting
                    "curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
                    # Use a temporary file, add curl -f to fail on server error, and verify content
                    f"curl -fsSL https://nvidia.github.io/libnvidia-container/{distro}/libnvidia-container.list -o /tmp/nvidia-container-toolkit.list",
                    # Verify downloaded file looks like a deb source list before proceeding
                    "grep -q '^deb ' /tmp/nvidia-container-toolkit.list || (echo 'ERROR: Downloaded nvidia source list is invalid!' && exit 1)",
                    "sed -i 's|deb https://|deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://|g' /tmp/nvidia-container-toolkit.list",
                    "mv /tmp/nvidia-container-toolkit.list /etc/apt/sources.list.d/nvidia-container-toolkit.list",
                    "apt-get update",
                    "apt-get install -y nvidia-container-toolkit",
                    "nvidia-ctk runtime configure --runtime=docker",
                    "systemctl restart docker" # This might fail if docker is not managed by systemd (e.g. rootless)
                ]
            elif distro in ['centos', 'rhel', 'fedora', 'rocky', 'almalinux']:
                 distro_repo = 'centos8' if distro in ['centos', 'rhel', 'rocky', 'almalinux'] and '8' in distro_raw else \
                               'fedora' if distro == 'fedora' else \
                               'centos7' # Default assumption or further detection needed
                 logger.info(f"[{context}] Using repo variant: {distro_repo}")
                 setup_commands = [
                    "dnf install -y curl", # Use dnf for modern versions
                    f"curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | tee /etc/yum.repos.d/nvidia-container-toolkit.repo",
                    "dnf install -y nvidia-container-toolkit",
                    "nvidia-ctk runtime configure --runtime=docker",
                    "systemctl restart docker"
                 ]
            else:
                logger.warning(f"[{context}] Unsupported distribution: {distro}, cannot set up NVIDIA Container Toolkit automatically")
                return False
                
            # Run setup commands sequentially, checking status
            for i, cmd in enumerate(setup_commands):
                logger.info(f"[{context}] Running setup step {i+1}/{len(setup_commands)}: {cmd.split(' ')[0]}...")
                # Most setup commands require root privileges
                exit_status, output, error = await run_setup_command(cmd, check_sudo=True)
                
                if exit_status != 0:
                    # Log error but continue if possible? No, likely fatal.
                    logger.error(f"[{context}] Error during setup step {i+1}: {error}. Command: {cmd}")
                    logger.error(f"[{context}] Stdout: {output}")
                    # Attempt to restart docker might fail if docker isn't running or setup failed earlier
                    if "systemctl restart docker" in cmd:
                         logger.warning(f"[{context}] Failed to restart docker service, maybe it was not running or setup failed.")
                    else:
                         return False # Fail early if core setup fails
                else:
                     logger.info(f"[{context}] Setup step {i+1} successful.")

            logger.info(f"[{context}] NVIDIA Container Toolkit setup commands completed, verifying...")
            
            # Verify setup by checking docker info again
            success = await GPUDetector.check_docker_gpu_support(ssh_client)
            
            if success:
                logger.info(f"[{context}] NVIDIA Container Toolkit setup verification successful")
            else:
                logger.warning(f"[{context}] NVIDIA Container Toolkit setup verification failed (nvidia runtime not detected in docker info)")
                
            return success
            
        except Exception as e:
            logger.error(f"[{context}] Error setting up NVIDIA Container Toolkit: {str(e)}", exc_info=True)
            return False 