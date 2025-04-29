"""
Example usage of the Container Manager for local or remote execution.
"""
import argparse
import asyncio
import logging
import os
import sys
from typing import Optional, Dict
import shlex

# Only import SSHClient if not running locally
# from paramiko import AutoAddPolicy, SSHClient 

# Add project root to sys.path to find container_manager module
# Adjust the path separators and number of ".." if necessary based on your structure
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Assuming example_usage.py is in the root or a known location relative to src
    # If example_usage.py is in root: go into src/miners/container_manager
    # project_root = os.path.abspath(os.path.join(script_dir, "src")) 
    # If example_usage.py is inside src/miners/container_manager: go up 3 levels
    project_root = os.path.abspath(os.path.join(script_dir, "..", "..")) 
    src_root = os.path.abspath(os.path.join(script_dir, "..", "..")) # Assuming src is one level up
    # Check if the calculated path is reasonable
    # if "container_manager" not in os.listdir(os.path.join(project_root, "container_manager")):
    #      print(f"Warning: Could not auto-detect project structure reliably from {script_dir}.")
    #      print(f"Calculated project_root: {project_root}")
    #      print(f"Make sure container_manager is importable.")
    
    # Use src_root if seems more appropriate based on typical structure
    if os.path.basename(src_root) == 'src':
         sys.path.insert(0, src_root)
         print(f"Adding {src_root} to sys.path")
    elif os.path.basename(project_root) == 'polaris-pod-service': # Check if parent is workspace root
         sys.path.insert(0, project_root)
         print(f"Adding {project_root} to sys.path")
    else:
         # Fallback or adjust based on actual structure
         sys.path.insert(0, script_dir) 
         print(f"Adding {script_dir} to sys.path as fallback.")
         
    from container_manager import ContainerManager, GPUDetector # Adjusted import path
    from paramiko import AutoAddPolicy, SSHClient # Keep import for type hinting and conditional use
except ImportError as e:
    print(f"Error importing ContainerManager: {e}")
    print(f"Current sys.path: {sys.path}")
    print("Ensure the script is run from the correct directory or project structure is correct.")
    sys.exit(1)
except FileNotFoundError:
    print("Error determining script path or project structure.")
    sys.exit(1)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s' # Added levelname
)
# Suppress overly verbose paramiko logs if desired
# logging.getLogger("paramiko").setLevel(logging.WARNING)
logger = logging.getLogger("example_usage")

# --- Refactored Example Functions ---

async def manage_container(local=False, container_type='basic', image_name=None, ssh_client=None, setup_user=True):
    """
    Create and manage a container based on the given profile.
    
    Args:
        local: Whether to run container operations locally (True) or via SSH (False)
        container_type: Type of container to create ('basic', 'gpu', 'dind')
        image_name: Docker image to use (overrides defaults for the container type)
        ssh_client: Optional SSHClient for remote operations
        setup_user: Whether to set up a 'pod-user' in the container
        
    Returns:
        Container info object if successful, None otherwise
    """
    # Create the container manager
    manager = ContainerManager(ssh_client=ssh_client)
    
    # Perform GPU detection
    gpu_info = {"has_gpu": False, "count": 0, "types": [], "has_toolkit": False}
    try:
        logger.info(f"[{manager.context}] Checking for GPU hardware and drivers...")
        has_gpu, gpu_info = await GPUDetector.detect_nvidia_gpu(manager.ssh_client)
        if has_gpu:
            logger.info(f"[{manager.context}] GPU detected: {gpu_info.get('count')} x {', '.join(gpu_info.get('types', ['Unknown']))}")
            logger.info(f"[{manager.context}] NVIDIA drivers installed: {gpu_info.get('has_drivers', False)}")
            logger.info(f"[{manager.context}] NVIDIA container toolkit installed: {gpu_info.get('has_toolkit', False)}")
        else:
            logger.info(f"[{manager.context}] No compatible GPU hardware detected")
    except Exception as e:
        logger.error(f"[{manager.context}] Error during GPU detection: {str(e)}")
        has_gpu = False
    
    # Set container configuration based on type and GPU availability
    enable_gpu = False
    if container_type == 'gpu':
        # For GPU containers, always try to enable GPU
        enable_gpu = has_gpu and gpu_info.get("has_drivers", False)
        if not enable_gpu:
            logger.warning(f"[{manager.context}] GPU container requested but no GPU/drivers detected. Container will be created without GPU access.")
        if not image_name:
            image_name = "nvidia/cuda:11.7.1-base-ubuntu22.04"
    elif container_type == 'dind':
        # Docker-in-Docker container (explicitly disable GPU for DinD containers)
        enable_gpu = False
        if not image_name:
            image_name = "docker:dind"
    else:
        # Basic container - enable GPU if available
        enable_gpu = has_gpu and gpu_info.get("has_drivers", False)
        if not image_name:
            # Select image based on GPU availability
            image_name = "nvidia/cuda:11.7.1-base-ubuntu22.04" if enable_gpu else "ubuntu:latest"
    
    logger.info(f"[{manager.context}] Creating container with image: {image_name}, GPU enabled: {enable_gpu}")
    
    # Create the container
    container_info = await manager.create_container(
        image=image_name,
        ports={"80": None},  # Map container port 80 to dynamic host port
        volumes=None,
        environment={"CONTAINER_TYPE": container_type},
        enable_gpu=enable_gpu,
        dind_enabled=(container_type == 'dind')
    )
    
    if container_info:
        logger.info(f"[{manager.context}] Container created successfully!")
        logger.info(f"[{manager.context}] Container ID: {container_info.container_id}")
        logger.info(f"[{manager.context}] Container Name: {container_info.container_name}")
        logger.info(f"[{manager.context}] Port Mappings: {container_info.ports}")
        
        # Verify GPU access if enabled
        if enable_gpu and container_info.gpu_enabled and container_info.status.lower() == "running":
            logger.info(f"[{manager.context}] Verifying GPU access in container...")
            
            # Run nvidia-smi in container (installation should have been done during container creation)
            verify_cmd = f"exec {shlex.quote(container_info.container_id)} nvidia-smi"
            exit_status, output, error = await manager._run_docker_command(verify_cmd)
            
            if exit_status == 0:
                logger.info(f"[{manager.context}] ✅ GPU access verified successfully!")
                # Print a formatted version of the output
                for line in output.splitlines():
                    if line.strip():
                        logger.info(f"  {line.strip()}")
            else:
                logger.warning(f"[{manager.context}] ❌ GPU access verification failed: {error}")
                logger.info(f"[{manager.context}] Attempting to install nvidia-smi manually...")
                
                # Try to install nvidia-utils package
                install_cmd = f"exec {shlex.quote(container_info.container_id)} bash -c 'apt-get update && apt-get install -y nvidia-utils-525 || apt-get install -y nvidia-utils-520 || apt-get install -y nvidia-utils-515'"
                exit_status, output, error = await manager._run_docker_command(install_cmd)
                
                if exit_status == 0:
                    logger.info(f"[{manager.context}] Installed NVIDIA utilities, retrying verification...")
                    
                    # Try verification again
                    verify_cmd = f"exec {shlex.quote(container_info.container_id)} nvidia-smi"
                    exit_status, output, error = await manager._run_docker_command(verify_cmd)
                    
                    if exit_status == 0:
                        logger.info(f"[{manager.context}] ✅ GPU access verified after manual installation!")
                        for line in output.splitlines():
                            if line.strip():
                                logger.info(f"  {line.strip()}")
                    else:
                        logger.error(f"[{manager.context}] ❌ GPU access still not working after manual installation: {error}")
                else:
                    logger.error(f"[{manager.context}] Failed to install NVIDIA utilities: {error}")
        
        logger.info(f"[{manager.context}] Container is running and ready to use.")
        logger.info(f"[{manager.context}] To access the container: docker exec -it {container_info.container_id} bash")
        
        return container_info
    else:
        logger.error(f"[{manager.context}] Failed to create container.")
        return None

# --- Main Execution Logic ---

async def main():
    parser = argparse.ArgumentParser(
        description="Container Manager Example - Create containers locally or via SSH.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Execution Mode
    parser.add_argument(
        "--local", 
        action="store_true", 
        help="Run container operations on the local machine instead of SSH."
    )
    
    # SSH Arguments (required only if --local is not set)
    ssh_group = parser.add_argument_group('SSH Connection Arguments (if not --local)')
    ssh_group.add_argument("--host", help="SSH host address.")
    ssh_group.add_argument("--username", help="SSH username.")
    ssh_group.add_argument("--password", help="SSH password (use key if possible).")
    ssh_group.add_argument("--key", help="Path to SSH private key file.")

    # Container Arguments
    container_group = parser.add_argument_group('Container Configuration')
    container_group.add_argument(
        "--type", 
        choices=["basic", "gpu", "dind"], 
        default="basic", 
        help="Type of container configuration profile."
    )
    container_group.add_argument("--image", help="Override default Docker image for the chosen type.")
    container_group.add_argument("--name", help="Specify a custom name for the container.")
    # Add more args for ports, volumes, env vars if needed, or use a config file approach
    
    args = parser.parse_args()
    
    ssh_client: Optional[SSHClient] = None
    
    # Validate arguments based on mode
    if args.local:
        logger.info("Running in Local mode.")
        if args.host or args.username or args.password or args.key:
             logger.warning("SSH arguments provided with --local flag are ignored.")
    else:
        logger.info("Running in SSH mode.")
        # SSH mode requires host and credentials
        if not args.host or not args.username:
            parser.error("--host and --username are required when not using --local.")
        if not args.password and not args.key:
            parser.error("Either --password or --key must be provided for SSH connection when not using --local.")
            
        # Setup SSH connection
        try:
            ssh_client = SSHClient()
            ssh_client.set_missing_host_key_policy(AutoAddPolicy())
            logger.info(f"Connecting to SSH host {args.host} as {args.username}...")
            if args.key:
                logger.info(f"Using SSH key: {args.key}")
                ssh_client.connect(args.host, username=args.username, key_filename=args.key, timeout=10)
            else:
                logger.info("Using SSH password.")
                ssh_client.connect(args.host, username=args.username, password=args.password, timeout=10)
            logger.info("SSH connection successful.")
        except Exception as e:
            logger.error(f"Failed to establish SSH connection to {args.host}: {e}", exc_info=True)
            sys.exit(1)
    
    # --- Run the container management task ---
    try:
         await manage_container(
              local=args.local,
              container_type=args.type,
              image_name=args.image,
              ssh_client=ssh_client,
              setup_user=True
         )
    finally:
         # Close SSH connection if it was opened
         if ssh_client:
             logger.info("Closing SSH connection.")
             ssh_client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Execution interrupted by user.")
        sys.exit(0) 