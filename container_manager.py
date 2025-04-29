"""
Container Manager - Main module for container creation and management
"""
import asyncio
import json
import logging
import random
import shlex
import string
import time
import os # Added for user ID check
from typing import Any, Dict, List, Optional, Tuple, Union

from paramiko.client import SSHClient

# Import the refactored helpers using absolute paths from package root
from gpu_detector import GPUDetector

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
    Manager for creating and managing Docker containers locally or via SSH.
    Supports:
    - GPU detection and passthrough
    - Docker-in-Docker (DinD) support
    - Port mapping and management
    - Assumes Docker commands require root privileges if running locally as non-root.
    """
    
    def __init__(self, ssh_client: Optional[SSHClient] = None):
        """
        Initialize the ContainerManager.

        Args:
            ssh_client: Connected Paramiko SSH client for remote operation, 
                        or None for local operation.
        """
        self.ssh_client = ssh_client
        self.containers = {}  # container_name -> ContainerInfo
        self.context = "SSH" if ssh_client else "Local"
        self.is_local_root = False
        if not self.ssh_client:
            try:
                # Check if running as root locally
                self.is_local_root = (os.geteuid() == 0)
            except AttributeError:
                # os.geteuid() might not be available on all systems (e.g., Windows)
                # Assume non-root if check fails
                self.is_local_root = False 
                logger.warning("Could not determine if running as root locally. Assuming non-root.")
        
        logger.info(f"ContainerManager initialized for {self.context} operation (Running as root: {self.is_local_root if not self.ssh_client else 'N/A'}).")
        
    async def _run_command(self, command: str) -> Tuple[int, str, str]:
        """
        Executes a command locally (with sudo if needed) or remotely via SSH.

        Args:
            command: The command string to execute.

        Returns:
            A tuple containing (exit_status, stdout, stderr).
        """
        full_command = command
        
        if self.ssh_client:
            # Execute remotely via SSH
            try:
                logger.debug(f"[{self.context}] Running remote command: {full_command}")
                stdin, stdout, stderr = self.ssh_client.exec_command(full_command, timeout=120) # Add timeout
                exit_status = stdout.channel.recv_exit_status()
                stdout_data = stdout.read().decode('utf-8').strip()
                stderr_data = stderr.read().decode('utf-8').strip()
                logger.debug(f"[{self.context}] Remote command exit: {exit_status}, stdout: {stdout_data[:100]}..., stderr: {stderr_data[:100]}...")
                return exit_status, stdout_data, stderr_data
            except Exception as e:
                logger.error(f"[{self.context}] Error executing remote command '{full_command}': {str(e)}", exc_info=True)
                return -1, "", str(e)
        else:
            # Execute locally
            # Prepend sudo if not running as root
            if not self.is_local_root:
                # Use non-interactive sudo (-n) to avoid prompts
                full_command = f"sudo -n {command}" 
            
            logger.debug(f"[{self.context}] Running local command: {full_command}")
            process = await asyncio.create_subprocess_shell(
                full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_bytes, stderr_bytes = await process.communicate()
            exit_status = process.returncode
            
            stdout_data = stdout_bytes.decode('utf-8').strip()
            stderr_data = stderr_bytes.decode('utf-8').strip()
            
            # Check if sudo failed due to password requirement
            if not self.is_local_root and exit_status != 0 and "sudo: a password is required" in stderr_data:
                 logger.error(f"[{self.context}] Sudo requires a password or is not configured for NOPASSWD. Command failed: {command}")
                 # Return a specific error message/status?
                 return exit_status, stdout_data, f"Sudo password required or NOPASSWD not configured. {stderr_data}"
                 
            logger.debug(f"[{self.context}] Local command exit: {exit_status}, stdout: {stdout_data[:100]}..., stderr: {stderr_data[:100]}...")
            return exit_status, stdout_data, stderr_data

    async def _run_docker_command(self, command: str) -> Tuple[int, str, str]:
        """Helper to prefix commands with 'docker' and run them."""
        return await self._run_command(f"docker {command}")
        
    async def create_container(self, 
                              image: str,
                              container_name: Optional[str] = None,
                              ports: Optional[Dict[str, str]] = None,
                              volumes: Optional[Dict[str, str]] = None,
                              environment: Optional[Dict[str, str]] = None,
                              enable_gpu: bool = True,
                              cpu_limit: Optional[str] = None,
                              memory_limit: Optional[str] = None,
                              network: Optional[str] = None,
                              dind_enabled: bool = False) -> Optional[ContainerInfo]:
        """
        Create a Docker container with specified configuration, locally or remotely.
        Assumes Docker commands require root privileges locally if run by non-root user.
        
        Args:
            image: Docker image to use
            container_name: Name for the container (generated if None)
            ports: Dictionary of port mappings {"container_port": "host_port"}
            volumes: Dictionary of volume mappings {"host_path": "container_path"}
            environment: Dictionary of environment variables
            enable_gpu: Whether to attempt GPU detection and passthrough.
            cpu_limit: CPU limit (e.g., "2")
            memory_limit: Memory limit (e.g., "4g")
            network: Docker network to use
            dind_enabled: Whether to enable Docker-in-Docker support.
            
        Returns:
            ContainerInfo object if successful, None otherwise
        """
        logger.info(f"[{self.context}] Creating container with image: {image}")
        
        try:
            # Generate container name if not provided
            if not container_name:
                prefix = "polaris-pod-"
                suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                timestamp = int(time.time())
                container_name = f"{prefix}{timestamp}-{suffix}"
                logger.info(f"[{self.context}] Generated container name: {container_name}")
            
            # Check for GPU if enabled
            gpu_info = {"has_gpu": False, "count": 0, "types": [], "has_toolkit": False}
            actual_enable_gpu = False # Flag if we actually enable GPU in run command
            if enable_gpu:
                logger.info(f"[{self.context}] Checking for GPU support...")
                has_gpu_hw, gpu_details = await GPUDetector.detect_nvidia_gpu(self.ssh_client)
                gpu_info = gpu_details # Store full details
                
                if has_gpu_hw and gpu_info["has_drivers"]:
                    logger.info(f"[{self.context}] NVIDIA GPU hardware and drivers detected.")
                    if not gpu_info["has_toolkit"]:
                        logger.info(f"[{self.context}] NVIDIA Container Toolkit runtime not detected in Docker, attempting setup...")
                        # Toolkit setup requires root/sudo
                        toolkit_setup_success = await GPUDetector.setup_nvidia_container_toolkit(self.ssh_client)
                        if toolkit_setup_success:
                            logger.info(f"[{self.context}] NVIDIA Container Toolkit setup successful. Re-checking Docker info...")
                            # Re-check toolkit status after setup attempt
                            gpu_info["has_toolkit"] = await GPUDetector.check_docker_gpu_support(self.ssh_client)
                        else:
                            logger.warning(f"[{self.context}] Failed to install/configure NVIDIA Container Toolkit. GPU support will be disabled.")
                    
                    if gpu_info["has_toolkit"]:
                        logger.info(f"[{self.context}] GPU support enabled: {gpu_info['count']} GPU(s) detected ({gpu_info['types'][0] if gpu_info['types'] else 'N/A'}).")
                        actual_enable_gpu = True
                    else:
                        logger.warning(f"[{self.context}] NVIDIA Container Toolkit runtime still not available after setup attempt. Disabling GPU.")
                        actual_enable_gpu = False
                else:
                    logger.info(f"[{self.context}] GPU support disabled: No compatible GPU hardware or drivers found.")
                    actual_enable_gpu = False
            else:
                 logger.info(f"[{self.context}] GPU support not requested.")
            
            # Prepare Docker run command parts
            run_cmd_parts = ["run", "-d"] # Base command for _run_docker_command
            
            # Add container name
            run_cmd_parts.extend(["--name", shlex.quote(container_name)])
            
            # Add resource limits
            if cpu_limit:
                run_cmd_parts.extend(["--cpus", shlex.quote(cpu_limit)])
            if memory_limit:
                run_cmd_parts.extend(["--memory", shlex.quote(memory_limit)])
                
            # Add GPU flags if enabled and available
            if actual_enable_gpu:
                 # Use --gpus=all (requires nvidia-container-toolkit correctly installed)
                run_cmd_parts.append("--gpus=all")
            
            # Add network if specified
            if network:
                run_cmd_parts.extend(["--network", shlex.quote(network)])
            
            # Add port mappings
            if ports:
                for container_port, host_port in ports.items():
                    # Basic validation
                    if str(container_port).isdigit():
                        if host_port and str(host_port).isdigit(): # Specific host port requested
                             run_cmd_parts.extend(["-p", f"{host_port}:{container_port}"])
                        elif host_port is None or str(host_port).strip() == "": # Dynamic host port requested
                             logger.info(f"[{self.context}] Mapping container port {container_port} to a random host port.")
                             run_cmd_parts.extend(["-p", str(container_port)])
                        else:
                             logger.warning(f"[{self.context}] Invalid host port value skipped for container port {container_port}: {host_port}")
                    else:
                         logger.warning(f"[{self.context}] Invalid container port skipped: {container_port}")
            
            # Add volume mappings
            if volumes:
                for host_path, container_path in volumes.items():
                    # Basic quoting for paths
                    run_cmd_parts.extend(["-v", f"{shlex.quote(host_path)}:{shlex.quote(container_path)}"])
            
            # Add environment variables
            if environment:
                for key, value in environment.items():
                    run_cmd_parts.extend(["-e", f"{shlex.quote(key)}={shlex.quote(value)}"])
            
            # Add Docker-in-Docker configuration if enabled
            if dind_enabled:
                logger.info(f"[{self.context}] Enabling Docker-in-Docker options.")
                # Add privileged mode (required for DinD)
                run_cmd_parts.append("--privileged")
                # Note: Mapping the host Docker socket is common but has security implications.
                # Consider alternatives if security is paramount.
                run_cmd_parts.extend(["-v", "/var/run/docker.sock:/var/run/docker.sock"]) 
                # Might need docker binary too depending on image
                # run_cmd_parts.extend(["-v", "/usr/bin/docker:/usr/bin/docker"]) 
                # Standard DinD images often handle TLS internally
                # run_cmd_parts.extend(["-e", "DOCKER_TLS_CERTDIR="])
            
            # Add the image
            run_cmd_parts.append(shlex.quote(image))

            # Add a keep-alive command for basic images that might exit immediately
            # Check if image name contains known base image names that need keep-alive
            if any(base in image for base in ["ubuntu", "nvidia/cuda", "docker:"]): 
                logger.info(f"[{self.context}] Adding 'tail -f /dev/null' to keep basic container running for image '{image}'.")
                run_cmd_parts.extend(["tail", "-f", "/dev/null"])
            
            # Construct the final command string (for logging)
            docker_run_args = " ".join(run_cmd_parts)
            logger.info(f"[{self.context}] Preparing Docker command: docker {docker_run_args}") # Log before execution
            
            # Run the command using the new helper
            exit_status, output, error = await self._run_docker_command(docker_run_args)
            
            if exit_status != 0:
                logger.error(f"[{self.context}] Error creating container '{container_name}': {error}")
                logger.error(f"[{self.context}] Docker command output: {output}") # Log output on error
                return None
            
            # Get container ID from output (should be stdout on success)
            container_id = output.strip()
            if not container_id or len(container_id) < 12: # Basic sanity check for ID format
                 logger.error(f"[{self.context}] Failed to get valid container ID from output: '{container_id}'")
                 logger.error(f"[{self.context}] Stderr from creation: {error}")
                 return None
                 
            logger.info(f"[{self.context}] Container '{container_name}' created with ID: {container_id[:12]}")
            
            # Verify container is running and get details
            await asyncio.sleep(2)  # Give it a moment to fully start
            
            inspect_cmd = f"inspect {container_id}"
            exit_status_insp, inspect_output, error_insp = await self._run_docker_command(inspect_cmd)
            
            if exit_status_insp != 0:
                logger.error(f"[{self.context}] Error inspecting container {container_id[:12]}: {error_insp}")
                # Container might have exited quickly, try to remove it?
                # For now, just return None
                return None
            
            # Parse container info
            container_info = None
            try:
                inspect_data_list = json.loads(inspect_output)
                if not inspect_data_list or not isinstance(inspect_data_list, list) or len(inspect_data_list) == 0:
                    logger.error(f"[{self.context}] Invalid container inspect data received: {inspect_output[:200]}")
                    return None
                
                inspect_data = inspect_data_list[0] # Get the first item
                
                # Extract container status
                status = inspect_data.get("State", {}).get("Status", "unknown")
                
                # Extract port mappings
                port_mappings = {}
                ports_info = inspect_data.get("NetworkSettings", {}).get("Ports", {})
                logger.debug(f"[{self.context}] Parsing ports_info: {ports_info}") # DEBUG LOG
                for container_port_proto, host_bindings in ports_info.items():
                    if host_bindings and isinstance(host_bindings, list) and len(host_bindings) > 0:
                        # Take the first host binding
                        host_ip = host_bindings[0].get("HostIp", "")
                        host_port = host_bindings[0].get("HostPort", "")
                        logger.debug(f"[{self.context}] Found binding for {container_port_proto}: HostIP='{host_ip}', HostPort='{host_port}'") # DEBUG LOG
                        if host_port:
                            # Extract just the port number from container_port (e.g., "80/tcp" -> "80")
                            container_port_num = container_port_proto.split("/")[0]
                            # Store mapping with host IP if available
                            mapping_value = f"{host_ip}:{host_port}" if host_ip and host_ip != "0.0.0.0" else host_port # Simplify if 0.0.0.0
                            port_mappings[container_port_num] = mapping_value
                            logger.debug(f"[{self.context}] Stored mapping: {container_port_num} -> {mapping_value}") # DEBUG LOG
                
                # Create container info object
                container_info = ContainerInfo(
                    container_id=container_id,
                    container_name=inspect_data.get("Name", "").lstrip("/"), # Get name from inspect
                    image=inspect_data.get("Config", {}).get("Image", image),
                    ports=port_mappings,
                    gpu_enabled=actual_enable_gpu, # Use the flag determined earlier
                    gpu_count=gpu_info["count"] if actual_enable_gpu else 0,
                    gpu_type=gpu_info["types"][0] if actual_enable_gpu and gpu_info["types"] else "None",
                    creation_time=time.time(), # Or parse from inspect_data.get("Created")
                    status=status
                )
                
                self.containers[container_info.container_name] = container_info
                logger.info(f"[{self.context}] Container '{container_info.container_name}' is {status}. Info: {container_info.to_dict()}")
                
                # If GPU was enabled and container is running, install nvidia tools inside if needed
                if container_info and actual_enable_gpu and status.lower() == "running":
                    logger.info(f"[{self.context}] GPU-enabled container created successfully. Installing NVIDIA tools inside...")
                    # Only attempt installation if container is running
                    await self._install_nvidia_tools_in_container(container_info.container_id)
                
            except json.JSONDecodeError:
                logger.error(f"[{self.context}] Failed to parse container inspect JSON data: {inspect_output[:200]}...")
                return None
            except Exception as e:
                logger.error(f"[{self.context}] Error processing container info: {str(e)}", exc_info=True)
                return None
            
            return container_info
            
        except Exception as e:
            logger.error(f"[{self.context}] Unexpected error during container creation: {str(e)}", exc_info=True)
            return None
    
    async def _install_nvidia_tools_in_container(self, container_id: str) -> bool:
        """
        Install NVIDIA tools (including nvidia-smi) inside a GPU-enabled container.
        
        Args:
            container_id: Container ID or name
            
        Returns:
            Boolean indicating success
        """
        logger.info(f"[{self.context}] Installing NVIDIA tools inside container {container_id[:12]}...")
        
        try:
            # First check if nvidia-smi exists already
            check_cmd = f"exec {shlex.quote(container_id)} which nvidia-smi || echo 'not-found'"
            exit_status, output, error = await self._run_docker_command(check_cmd)
            
            if exit_status == 0 and 'not-found' not in output:
                logger.info(f"[{self.context}] nvidia-smi already available in container {container_id[:12]}, skipping installation.")
                return True
                
            # Determine container OS (Ubuntu/Debian is assumed for now)
            get_os_cmd = f"exec {shlex.quote(container_id)} cat /etc/os-release || echo 'unknown'"
            exit_status, os_info, error = await self._run_docker_command(get_os_cmd)
            
            if 'ubuntu' in os_info.lower() or 'debian' in os_info.lower():
                # Ubuntu/Debian detected
                logger.info(f"[{self.context}] Ubuntu/Debian-based container detected, using apt-get for NVIDIA tools.")
                
                # Commands to install NVIDIA tools
                install_commands = [
                    "apt-get update",
                    # Try multiple approaches
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cuda-drivers-* || echo 'cuda-drivers not found'",
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nvidia-utils-* || echo 'nvidia-utils not found'",
                    # If still not installed, try another approach with repository setup
                    "if ! which nvidia-smi > /dev/null; then " +
                    "apt-get install -y --no-install-recommends gnupg curl ca-certificates && " +
                    "rm -rf /var/lib/apt/lists/* && " +
                    "curl -fsSL https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/3bf863cc.pub | apt-key add - && " +
                    "echo 'deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/ /' > /etc/apt/sources.list.d/cuda.list && " +
                    "apt-get update && " +
                    "apt-get install -y --no-install-recommends nvidia-utils-525 && " +
                    "rm -rf /var/lib/apt/lists/*; " +
                    "fi"
                ]
                
                # Execute commands
                combined_cmd = " && ".join(install_commands)
                exec_cmd = f"exec {shlex.quote(container_id)} bash -c {shlex.quote(combined_cmd)}"
                
                logger.info(f"[{self.context}] Running NVIDIA tools installation commands in container...")
                exit_status, output, error = await self._run_docker_command(exec_cmd)
                
                if exit_status != 0:
                    logger.warning(f"[{self.context}] Some NVIDIA tools installation steps failed: {error}")
                    # Continue despite errors, as nvidia-smi might still work
                
                # Verify nvidia-smi is now available
                verify_cmd = f"exec {shlex.quote(container_id)} which nvidia-smi || echo 'not-found'"
                exit_status, output, error = await self._run_docker_command(verify_cmd)
                
                if exit_status == 0 and 'not-found' not in output:
                    logger.info(f"[{self.context}] NVIDIA tools installed successfully in container {container_id[:12]}.")
                    
                    # Run nvidia-smi to verify GPU access
                    smi_cmd = f"exec {shlex.quote(container_id)} nvidia-smi"
                    exit_status, smi_output, smi_error = await self._run_docker_command(smi_cmd)
                    
                    if exit_status == 0:
                        logger.info(f"[{self.context}] nvidia-smi verified working inside container!")
                        return True
                    else:
                        logger.warning(f"[{self.context}] nvidia-smi installed but failed to run inside container: {smi_error}")
                        return False
                else:
                    logger.warning(f"[{self.context}] Failed to install nvidia-smi in container despite attempts.")
                    return False
            else:
                # Not Ubuntu/Debian, might need different approach
                logger.warning(f"[{self.context}] Container OS not recognized as Ubuntu/Debian, NVIDIA tools installation skipped.")
                return False
                
        except Exception as e:
            logger.error(f"[{self.context}] Error installing NVIDIA tools in container: {str(e)}", exc_info=True)
            return False

    async def setup_pod_user(self, 
                            container_id: str, 
                            username: str = "pod-user", 
                            password: Optional[str] = None,
                            setup_dind_user: bool = False) -> bool:
        """
        Set up a non-root user inside the specified container.
        Installs sudo, mosh, creates user, sets password, configures NOPASSWD sudo.
        Optionally adds user to docker group inside container (for DinD usage).
        Assumes Docker commands require root privileges locally if run by non-root user.
        
        Args:
            container_id: Container ID or name
            username: Username to create
            password: Password for the user (generated if None)
            setup_dind_user: If True, add user to docker group inside container.
            
        Returns:
            Boolean indicating success
        """
        logger.info(f"[{self.context}] Setting up user '{username}' in container {container_id[:12]}")
        
        try:
            # Generate random password if not provided
            if not password:
                password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
                logger.info(f"[{self.context}] Generated random password for {username}")
            
            # Use shell module functions for safer command construction inside container
            safe_username = shlex.quote(username)
            safe_password = shlex.quote(password)

            # Base commands for user setup inside container
            setup_commands_base = [
                "apt-get update",
                "apt-get install -y --no-install-recommends sudo mosh",
                f"useradd -m -s /bin/bash {safe_username}",
                # Use tee to write sudoers rule to avoid redirection issues
                f"echo \"{safe_username} ALL=(ALL) NOPASSWD:ALL\" | tee /etc/sudoers.d/{safe_username}",
                f"chmod 0440 /etc/sudoers.d/{safe_username}",
                # Use chpasswd utility
                f"echo \"{safe_username}:{safe_password}\" | chpasswd",
                # Create .ssh directory for potential future use
                f"mkdir -p /home/{safe_username}/.ssh",
                f"chown {safe_username}:{safe_username} /home/{safe_username}/.ssh",
                f"chmod 700 /home/{safe_username}/.ssh",
            ]
            
            # Commands for setting up user for DinD
            dind_commands = []
            if setup_dind_user:
                 logger.info(f"[{self.context}] Adding DinD setup steps for user '{username}'...")
                 dind_commands = [
                      # Check if docker group exists, create if not
                      "getent group docker || groupadd docker",
                      # Add user to docker group
                      f"usermod -aG docker {safe_username}",
                      # Note: Rootless setup inside DinD container is complex and often not needed
                      # If the host socket is mounted, the user needs group access on host or ACLs.
                      # If using dedicated DinD, the inner daemon runs as root.
                 ]

            # Combine commands into a single script to run with bash -c
            all_commands = setup_commands_base + dind_commands
            setup_script = " && ".join(all_commands)
            
            # Run the script inside the container using docker exec
            logger.info(f"[{self.context}] Running user setup script in container {container_id[:12]}...")
            # Ensure the script is properly quoted for the outer shell
            exec_cmd = f"exec {shlex.quote(container_id)} bash -c {shlex.quote(setup_script)}"
            
            exit_status, output, error = await self._run_docker_command(exec_cmd)
            
            if exit_status != 0:
                logger.error(f"[{self.context}] Error setting up user '{username}' in container {container_id[:12]}: {error}")
                logger.error(f"[{self.context}] User setup script output: {output}")
                return False
            
            logger.info(f"[{self.context}] User '{username}' set up successfully in container {container_id[:12]}")
            return True
            
        except Exception as e:
            logger.error(f"[{self.context}] Unexpected error setting up user in container: {str(e)}", exc_info=True)
            return False
    
    async def check_container_status(self, container_id: str) -> Tuple[bool, str]:
        """
        Check if a container is running.
        Assumes Docker commands require root privileges locally if run by non-root user.
        
        Args:
            container_id: Container ID or name
            
        Returns:
            Tuple of (is_running, status_string)
        """
        logger.info(f"[{self.context}] Checking status of container {container_id[:12]}")
        
        try:
            # Format uses Go template syntax
            inspect_format = "{{.State.Status}}"
            inspect_cmd = f"inspect --format='{inspect_format}' {shlex.quote(container_id)}"
            
            exit_status, output, error = await self._run_docker_command(inspect_cmd)
            
            if exit_status != 0:
                # If inspect fails, container likely doesn't exist or isn't accessible
                logger.warning(f"[{self.context}] Error checking container status for {container_id[:12]}: {error}. Assuming not running/exists.")
                return False, "not found"
            
            status = output.strip()
            logger.info(f"[{self.context}] Container {container_id[:12]} status: {status}")
            
            is_running = status.lower() == "running"
            return is_running, status
            
        except Exception as e:
            logger.error(f"[{self.context}] Unexpected error checking container status: {str(e)}", exc_info=True)
            return False, "error"
    
    async def stop_container(self, 
                           container_id: str,
                           timeout: int = 10) -> bool:
        """
        Stop a container.
        Assumes Docker commands require root privileges locally if run by non-root user.
        
        Args:
            container_id: Container ID or name
            timeout: Seconds to wait for stop before killing.
            
        Returns:
            Boolean indicating success
        """
        logger.info(f"[{self.context}] Stopping container {container_id[:12]} (timeout={timeout}s)")
        
        try:
            stop_cmd = f"stop -t {timeout} {shlex.quote(container_id)}"
            exit_status, output, error = await self._run_docker_command(stop_cmd)
            
            if exit_status != 0:
                # Check if error indicates already stopped
                if "No such container" in error:
                    logger.warning(f"[{self.context}] Container {container_id[:12]} already stopped or removed.")
                    # Update local cache if needed
                    for name, info in list(self.containers.items()):
                        if info.container_id == container_id:
                             info.status = "removed"
                             break
                    return True # Considered success if already stopped
                else:
                    logger.error(f"[{self.context}] Error stopping container {container_id[:12]}: {error}")
                return False
            
            stopped_id = output.strip()
            logger.info(f"[{self.context}] Container {stopped_id[:12]} stopped successfully.")
            
            # Update container info cache
            for name, info in list(self.containers.items()):
                 if info.container_id == container_id or info.container_name == stopped_id or name == stopped_id:
                    info.status = "exited" # More specific than stopped
                    logger.debug(f"[{self.context}] Updated status for container '{name}' to exited.")
                    break
            
            return True
            
        except Exception as e:
            logger.error(f"[{self.context}] Unexpected error stopping container: {str(e)}", exc_info=True)
            return False
    
    async def remove_container(self, 
                             container_id: str,
                             force: bool = False) -> bool:
        """
        Remove a container.
        Assumes Docker commands require root privileges locally if run by non-root user.
        
        Args:
            container_id: Container ID or name
            force: Whether to force removal of a running container.
            
        Returns:
            Boolean indicating success
        """
        logger.info(f"[{self.context}] Removing container {container_id[:12]} (force={force})")
        
        try:
            rm_cmd_parts = ["rm"]
            if force:
                rm_cmd_parts.append("-f")
            rm_cmd_parts.append(shlex.quote(container_id))
            rm_cmd = " ".join(rm_cmd_parts)
            
            exit_status, output, error = await self._run_docker_command(rm_cmd)
            
            if exit_status != 0:
                # Check if error indicates already removed
                if "No such container" in error:
                     logger.warning(f"[{self.context}] Container {container_id[:12]} already removed.")
                     # Update local cache if needed
                     removed_name = None
                     for name, info in list(self.containers.items()):
                         if info.container_id == container_id:
                              removed_name = name
                              break
                     if removed_name:
                          del self.containers[removed_name]
                          logger.debug(f"[{self.context}] Removed '{removed_name}' from local cache.")
                     return True # Considered success
                else:
                     logger.error(f"[{self.context}] Error removing container {container_id[:12]}: {error}")
                return False
            
            removed_id = output.strip()
            logger.info(f"[{self.context}] Container {removed_id[:12]} removed successfully.")
            
            # Remove container info from cache
            removed_name = None
            for name, info in list(self.containers.items()):
                 if info.container_id == container_id or info.container_name == removed_id or name == removed_id:
                     removed_name = name
                     break
            if removed_name:
                 del self.containers[removed_name]
                 logger.debug(f"[{self.context}] Removed '{removed_name}' from local cache.")
            
            return True
            
        except Exception as e:
            logger.error(f"[{self.context}] Unexpected error removing container: {str(e)}", exc_info=True)
            return False 