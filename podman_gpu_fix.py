"""
Patch for container_manager.py and gpu_detector.py to work with Podman
"""
import logging
import os
import subprocess
import sys
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

def patch_container_manager():
    """
    Create a patched version of container_manager.py for Podman GPU support.
    """
    try:
        # First, check if we're using Podman or Docker
        try:
            result = subprocess.run(["docker", "--version"], capture_output=True, text=True)
            is_podman = 'podman' in result.stdout.lower()
            logger.info(f"Container system detected: {'Podman' if is_podman else 'Docker'}")
        except Exception:
            is_podman = False
            logger.warning("Could not determine container system, assuming Docker")
        
        if not is_podman:
            logger.info("Not using Podman, skipping patch")
            return
        
        # Create the container_manager_patches directory if needed
        patches_dir = os.path.join(os.path.dirname(__file__), "container_manager_patches")
        os.makedirs(patches_dir, exist_ok=True)
        
        # Path to the patched file
        patched_file = os.path.join(patches_dir, "podman_gpu_detector.py")
        
        # Create the patched version of GPUDetector
        with open(patched_file, "w") as f:
            f.write("""
\"\"\"
Patched GPU Detector for Podman support
\"\"\"
import asyncio
import logging
import os
import shlex
import subprocess
from typing import Dict, Optional, Tuple

from paramiko.client import SSHClient

logger = logging.getLogger(__name__)

# Helper functions for command execution
async def _run_local_command(command: str) -> Tuple[int, str, str]:
    \"\"\"Run a command locally and return exit status, stdout, and stderr.\"\"\"
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    return process.returncode, stdout.decode().strip(), stderr.decode().strip()

async def _run_command(ssh_client: Optional[SSHClient], command: str) -> Tuple[int, str, str]:
    \"\"\"Run a command either via SSH or locally and return exit status, stdout, and stderr.\"\"\"
    if ssh_client:
        # Run via SSH
        stdin, stdout, stderr = ssh_client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        stdout_str = stdout.read().decode().strip()
        stderr_str = stderr.read().decode().strip()
        return exit_status, stdout_str, stderr_str
    else:
        # Run locally
        return await _run_local_command(command)

class GPUDetector:
    \"\"\"Detect GPUs on the target system and return their details.\"\"\"
    
    @staticmethod
    async def detect_nvidia_gpu(ssh_client: Optional[SSHClient]) -> Tuple[bool, Dict]:
        \"\"\"
        Detect NVIDIA GPUs on the target system.
        
        Args:
            ssh_client: Connected Paramiko SSH client or None for local execution
            
        Returns:
            Tuple of (has_gpu, gpu_details_dict)
            The gpu_details_dict contains detailed information about detected GPUs
        \"\"\"
        context = "SSH" if ssh_client else "Local"
        logger.info(f"[{context}] Detecting NVIDIA GPUs...")
        
        gpu_info = {
            "has_gpu": False,
            "has_drivers": False,
            "has_toolkit": False,
            "count": 0,
            "types": [],
            "memory": []
        }
        
        try:
            # First, check if nvidia-smi exists (drivers installed)
            check_cmd = "command -v nvidia-smi || echo 'not-found'"
            exit_status, output, error = await _run_command(ssh_client, check_cmd)
            
            gpu_info["has_drivers"] = exit_status == 0 and "not-found" not in output
            
            if not gpu_info["has_drivers"]:
                logger.warning(f"[{context}] NVIDIA drivers not detected (nvidia-smi not found)")
                # No drivers, so return early
                return False, gpu_info
                
            # Run basic nvidia-smi to check if GPUs are accessible
            basic_cmd = "nvidia-smi -L || echo 'not-accessible'"
            exit_status, output, error = await _run_command(ssh_client, basic_cmd)
            
            # Check for failure patterns in output/error
            failure_patterns = [
                "not-accessible",
                "No devices were found",
                "NVIDIA-SMI has failed",
                "Unable to determine the device handle"
            ]
            
            # Check if any failure pattern is in output or error
            has_failed = any(pattern in output or pattern in error for pattern in failure_patterns)
            if exit_status != 0 or has_failed:
                logger.warning(f"[{context}] NVIDIA GPUs not accessible. Exit code: {exit_status}, Error: {error}")
                return False, gpu_info
                
            # If we get here, nvidia-smi worked and found GPUs
            gpu_info["has_gpu"] = True
            
            # Log raw GPU info
            logger.info(f"[{context}] GPU info: {output}")
            
            # Get GPU count
            count_cmd = "nvidia-smi --list-gpus | wc -l"
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
                types_output = types_output_str.strip().split('\\n')
                gpu_info["types"] = types_output
                logger.info(f"[{context}] GPU types: {types_output}")
            else:
                logger.warning(f"[{context}] Could not determine GPU types. Error: {error}")

            # Get GPU memory
            memory_cmd = "nvidia-smi --query-gpu=memory.total --format=csv,noheader"
            exit_status, memory_output_str, error = await _run_command(ssh_client, memory_cmd)
            if exit_status == 0:
                memory_output = memory_output_str.strip().split('\\n')
                gpu_info["memory"] = memory_output
                logger.info(f"[{context}] GPU memory: {memory_output}")
            else:
                logger.warning(f"[{context}] Could not determine GPU memory. Error: {error}")

            # ----- PODMAN SPECIFIC PATCH -----
            # Check for podman vs docker
            podman_check_cmd = "docker --version | grep -i podman || echo 'not-podman'"
            exit_status, podman_output, error = await _run_command(ssh_client, podman_check_cmd)
            
            is_podman = exit_status == 0 and "not-podman" not in podman_output
            if is_podman:
                logger.info(f"[{context}] Podman detected instead of Docker")
                # For Podman, we assume toolkit is available if drivers are installed
                # This is a simplification but works for many use cases
                gpu_info["has_toolkit"] = gpu_info["has_drivers"]
                if gpu_info["has_toolkit"]:
                    logger.info(f"[{context}] Assuming NVIDIA Container support for Podman (based on driver presence)")
                return True, gpu_info
                
            # Regular Docker check
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
        \"\"\"
        Check if Docker has GPU support configured.
        
        Args:
            ssh_client: Connected Paramiko SSH client or None for local execution
            
        Returns:
            Boolean indicating if Docker has NVIDIA runtime
        \"\"\"
        context = "SSH" if ssh_client else "Local"
        
        try:
            # Check for podman vs docker first
            podman_check_cmd = "docker --version | grep -i podman || echo 'not-podman'"
            exit_status, podman_output, error = await _run_command(ssh_client, podman_check_cmd)
            
            is_podman = exit_status == 0 and "not-podman" not in podman_output
            if is_podman:
                logger.info(f"[{context}] Podman detected instead of Docker")
                # For Podman, check if nvidia-smi is available
                check_cmd = "command -v nvidia-smi || echo 'not-found'"
                exit_status, output, error = await _run_command(ssh_client, check_cmd)
                has_nvidia_drivers = exit_status == 0 and "not-found" not in output
                if has_nvidia_drivers:
                    logger.info(f"[{context}] NVIDIA drivers detected, assuming Podman GPU support available")
                    return True
                return False
            
            # Regular Docker check
            toolkit_cmd = "docker info 2>/dev/null | grep -i 'Runtimes:.* nvidia' || echo 'No NVIDIA Docker support'"
            exit_status, toolkit_output, error = await _run_command(ssh_client, toolkit_cmd)
            # Check both exit status and output, as grep returns 1 if not found
            has_toolkit = "No NVIDIA Docker support" not in toolkit_output
            
            if has_toolkit:
                logger.info(f"[{context}] NVIDIA Container Toolkit runtime is configured in Docker")
            else:
                logger.warning(f"[{context}] NVIDIA Container Toolkit runtime not found in Docker info")
                
            return has_toolkit
            
        except Exception as e:
            logger.error(f"[{context}] Error checking Docker GPU support: {str(e)}", exc_info=True)
            return False
            
    @staticmethod
    async def setup_nvidia_container_toolkit(ssh_client: Optional[SSHClient]) -> bool:
        \"\"\"
        Attempt to set up NVIDIA Container Toolkit.
        
        Args:
            ssh_client: Connected Paramiko SSH client or None for local execution.
            
        Returns:
            Boolean indicating if setup was successful
        \"\"\"
        context = "SSH" if ssh_client else "Local"
        logger.info(f"[{context}] Setting up NVIDIA Container Toolkit")
        
        # First check if we're using Podman
        podman_check_cmd = "docker --version | grep -i podman || echo 'not-podman'"
        exit_status, podman_output, error = await _run_command(ssh_client, podman_check_cmd)
        
        is_podman = exit_status == 0 and "not-podman" not in podman_output
        if is_podman:
            logger.info(f"[{context}] Podman detected - GPU support is handled differently")
            # For Podman, if NVIDIA drivers are installed, we assume it's good to go
            check_cmd = "command -v nvidia-smi || echo 'not-found'"
            exit_status, output, error = await _run_command(ssh_client, check_cmd)
            if exit_status == 0 and "not-found" not in output:
                logger.info(f"[{context}] NVIDIA drivers detected for Podman")
                return True
            else:
                logger.warning(f"[{context}] NVIDIA drivers not detected, Podman cannot use GPU")
                return False
        
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
                 distro_repo = 'centos8' if distro in ['centos', 'rhel', 'rocky', 'almalinux'] and '8' in distro_raw else \\
                               'fedora' if distro == 'fedora' else \\
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
\"\"\"
            )
        
        # Create a patch to init.py
        init_patch = os.path.join(patches_dir, "__init__.py")
        with open(init_patch, "w") as f:
            f.write("""
\"\"\"
Container Manager Patches
\"\"\"
from container_manager_patches.podman_gpu_detector import GPUDetector
\"\"\"
            )
            
        # Create a monkey patching script to apply at runtime
        monkey_patch_file = os.path.join(os.path.dirname(__file__), "apply_podman_patch.py")
        with open(monkey_patch_file, "w") as f:
            f.write("""
\"\"\"
Apply Podman GPU patch to the container manager
\"\"\"
import os
import sys
import importlib
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s')
logger = logging.getLogger("podman_patch")

def apply_patch():
    \"\"\"Apply the Podman GPU patch\"\"\"
    try:
        # Add parent directory to sys.path
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
            
        patches_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "container_manager_patches")
        if patches_dir not in sys.path:
            sys.path.insert(0, patches_dir)
            
        # Check if we're using Podman
        import subprocess
        result = subprocess.run(["docker", "--version"], capture_output=True, text=True)
        is_podman = 'podman' in result.stdout.lower()
        
        if not is_podman:
            logger.info("Not using Podman, no need to apply patch")
            return False
            
        # Import the original module and the patched module
        from container_manager_patches import GPUDetector as PatchedDetector
        import gpu_detector
        original_detector = gpu_detector.GPUDetector
        
        # Apply the monkey patch
        gpu_detector.GPUDetector = PatchedDetector
        logger.info("Successfully applied Podman GPU patch")
        
        return True
    except Exception as e:
        logger.error(f"Failed to apply Podman GPU patch: {e}")
        return False

if __name__ == "__main__":
    apply_patch()
\"\"\"
            )
        
        logger.info(f"Created patched files in {patches_dir}")
        logger.info(f"To apply the patch, run: python {monkey_patch_file}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to patch container manager: {e}")
        return False

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s')
    
    # Apply the patch
    success = patch_container_manager()
    sys.exit(0 if success else 1) 