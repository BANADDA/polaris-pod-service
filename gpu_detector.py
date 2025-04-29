"""
GPU Detection Module - Detects availability of GPUs on the host system
"""
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Union

from paramiko.client import SSHClient

logger = logging.getLogger(__name__)

class GPUDetector:
    """Detect GPUs on the target system and return their details."""
    
    @staticmethod
    async def detect_nvidia_gpu(ssh_client: SSHClient) -> Tuple[bool, Dict]:
        """
        Detect if NVIDIA GPUs are available on the system.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            
        Returns:
            Tuple of (has_gpu, gpu_info)
                has_gpu: Boolean indicating if NVIDIA GPU is available
                gpu_info: Dictionary with GPU details (count, types, memory, etc.)
        """
        logger.info("Checking for NVIDIA GPU and drivers")
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
            stdin, stdout, stderr = ssh_client.exec_command(has_gpu_cmd)
            has_gpu_output = stdout.read().decode().strip()
            gpu_info["has_gpu"] = "No NVIDIA GPU found" not in has_gpu_output
            
            if not gpu_info["has_gpu"]:
                logger.info("No NVIDIA GPU hardware detected")
                return False, gpu_info
                
            logger.info(f"NVIDIA GPU hardware detected: {has_gpu_output}")
            
            # Check if nvidia-smi is available (drivers installed)
            driver_cmd = "nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null || echo 'No NVIDIA drivers'"
            stdin, stdout, stderr = ssh_client.exec_command(driver_cmd)
            driver_output = stdout.read().decode().strip()
            gpu_info["has_drivers"] = "No NVIDIA drivers" not in driver_output
            
            if not gpu_info["has_drivers"]:
                logger.warning("NVIDIA GPU hardware detected but drivers not installed")
                return True, gpu_info  # GPU exists but no drivers
                
            # Get driver version
            gpu_info["driver_version"] = driver_output
            logger.info(f"NVIDIA drivers detected, version: {driver_output}")
            
            # Get CUDA version
            cuda_cmd = "nvidia-smi --query-gpu=cuda_version --format=csv,noheader 2>/dev/null || echo 'No CUDA'"
            stdin, stdout, stderr = ssh_client.exec_command(cuda_cmd)
            cuda_output = stdout.read().decode().strip()
            if "No CUDA" not in cuda_output:
                gpu_info["cuda_version"] = cuda_output
                logger.info(f"CUDA version: {cuda_output}")
            
            # Get GPU count
            count_cmd = "nvidia-smi --query-gpu=name --format=csv,noheader | wc -l"
            stdin, stdout, stderr = ssh_client.exec_command(count_cmd)
            count_output = stdout.read().decode().strip()
            try:
                gpu_info["count"] = int(count_output)
                logger.info(f"Number of NVIDIA GPUs: {count_output}")
            except ValueError:
                gpu_info["count"] = 1  # Assume at least one if parsing fails
                logger.warning(f"Could not parse GPU count, assuming 1. Output: {count_output}")
            
            # Get GPU types
            types_cmd = "nvidia-smi --query-gpu=name --format=csv,noheader"
            stdin, stdout, stderr = ssh_client.exec_command(types_cmd)
            types_output = stdout.read().decode().strip().split('\n')
            gpu_info["types"] = types_output
            logger.info(f"GPU types: {types_output}")
            
            # Get GPU memory
            memory_cmd = "nvidia-smi --query-gpu=memory.total --format=csv,noheader"
            stdin, stdout, stderr = ssh_client.exec_command(memory_cmd)
            memory_output = stdout.read().decode().strip().split('\n')
            gpu_info["memory"] = memory_output
            logger.info(f"GPU memory: {memory_output}")
            
            # Check for nvidia-container-toolkit (required for GPU in Docker)
            toolkit_cmd = "docker info | grep -i nvidia || echo 'No NVIDIA Docker support'"
            stdin, stdout, stderr = ssh_client.exec_command(toolkit_cmd)
            toolkit_output = stdout.read().decode().strip()
            gpu_info["has_toolkit"] = "No NVIDIA Docker support" not in toolkit_output
            
            if gpu_info["has_toolkit"]:
                logger.info("NVIDIA Container Toolkit is installed and working")
            else:
                logger.warning("NVIDIA drivers found but NVIDIA Container Toolkit is missing")
            
            return True, gpu_info
            
        except Exception as e:
            logger.error(f"Error detecting NVIDIA GPU: {str(e)}", exc_info=True)
            return gpu_info["has_gpu"], gpu_info
    
    @staticmethod
    async def check_docker_gpu_support(ssh_client: SSHClient) -> bool:
        """
        Check if Docker has GPU support configured.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            
        Returns:
            Boolean indicating if Docker has GPU support
        """
        logger.info("Checking Docker GPU support")
        try:
            # Check Docker GPU support
            cmd = "docker info | grep -i nvidia || echo 'No NVIDIA Docker support'"
            stdin, stdout, stderr = ssh_client.exec_command(cmd)
            output = stdout.read().decode().strip()
            has_support = "No NVIDIA Docker support" not in output
            
            if has_support:
                logger.info("Docker has NVIDIA GPU support")
            else:
                logger.warning("Docker does not have NVIDIA GPU support")
                
            return has_support
        except Exception as e:
            logger.error(f"Error checking Docker GPU support: {str(e)}", exc_info=True)
            return False
            
    @staticmethod
    async def setup_nvidia_container_toolkit(ssh_client: SSHClient) -> bool:
        """
        Attempt to set up NVIDIA Container Toolkit.
        
        Args:
            ssh_client: Connected Paramiko SSH client
            
        Returns:
            Boolean indicating if setup was successful
        """
        logger.info("Setting up NVIDIA Container Toolkit")
        try:
            # First check for distribution
            distro_cmd = "cat /etc/os-release | grep -i '^ID=' | cut -d= -f2"
            stdin, stdout, stderr = ssh_client.exec_command(distro_cmd)
            distro = stdout.read().decode().strip().lower().replace('"', '')
            
            logger.info(f"Detected Linux distribution: {distro}")
            
            setup_commands = []
            
            if distro in ['ubuntu', 'debian']:
                setup_commands = [
                    "export DEBIAN_FRONTEND=noninteractive",
                    "apt-get update",
                    "apt-get install -y curl ca-certificates gnupg",
                    "curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
                    f"curl -s -L https://nvidia.github.io/libnvidia-container/{distro}/libnvidia-container.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list",
                    "apt-get update",
                    "apt-get install -y nvidia-container-toolkit",
                    "nvidia-ctk runtime configure --runtime=docker",
                    "systemctl restart docker"
                ]
            elif distro in ['centos', 'rhel', 'fedora', 'rocky', 'almalinux']:
                setup_commands = [
                    "dnf install -y curl",
                    "curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | tee /etc/yum.repos.d/nvidia-container-toolkit.repo",
                    "dnf install -y nvidia-container-toolkit",
                    "nvidia-ctk runtime configure --runtime=docker",
                    "systemctl restart docker"
                ]
            else:
                logger.warning(f"Unsupported distribution: {distro}, cannot set up NVIDIA Container Toolkit automatically")
                return False
                
            # Run setup commands
            combined_cmd = " && ".join(setup_commands)
            logger.info(f"Running NVIDIA Container Toolkit setup command: {combined_cmd}")
            
            # Use sudo if available
            stdin, stdout, stderr = ssh_client.exec_command("command -v sudo && echo 'sudo available' || echo 'no sudo'")
            if "sudo available" in stdout.read().decode().strip():
                combined_cmd = "sudo bash -c '" + combined_cmd.replace("'", "'\\''") + "'"
            
            stdin, stdout, stderr = ssh_client.exec_command(combined_cmd)
            exit_status = stdout.channel.recv_exit_status()
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            
            if exit_status != 0:
                logger.error(f"Error setting up NVIDIA Container Toolkit: {error}")
                return False
                
            logger.info("NVIDIA Container Toolkit setup completed, checking if it works")
            
            # Verify setup
            toolkit_cmd = "docker info | grep -i nvidia || echo 'No NVIDIA Docker support'"
            stdin, stdout, stderr = ssh_client.exec_command(toolkit_cmd)
            toolkit_output = stdout.read().decode().strip()
            success = "No NVIDIA Docker support" not in toolkit_output
            
            if success:
                logger.info("NVIDIA Container Toolkit setup successful")
            else:
                logger.warning("NVIDIA Container Toolkit setup failed")
                
            return success
            
        except Exception as e:
            logger.error(f"Error setting up NVIDIA Container Toolkit: {str(e)}", exc_info=True)
            return False 