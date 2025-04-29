"""
Example usage of the Container Manager
"""
import argparse
import asyncio
import logging
import os
import sys

from paramiko import AutoAddPolicy, SSHClient

# Add the project root to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
sys.path.insert(0, project_root)

from src.miners.container_manager.container_manager import ContainerManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def create_basic_container(
    host: str, 
    username: str, 
    password: str = None, 
    key_path: str = None, 
    image: str = "ubuntu:latest"
):
    """
    Create a basic container without GPU support.
    
    Args:
        host: SSH host to connect to
        username: SSH username
        password: SSH password (optional)
        key_path: Path to SSH private key file (optional)
        image: Docker image to use
    """
    logger.info(f"Creating basic container on {host} using image {image}")
    
    # Setup SSH connection
    ssh_client = SSHClient()
    ssh_client.set_missing_host_key_policy(AutoAddPolicy())
    
    try:
        if key_path:
            ssh_client.connect(host, username=username, key_filename=key_path)
        else:
            ssh_client.connect(host, username=username, password=password)
            
        logger.info(f"Connected to {host} as {username}")
        
        # Create container manager
        manager = ContainerManager()
        
        # Create a basic container
        container_info = await manager.create_container(
            ssh_client=ssh_client,
            image=image,
            ports={"80": "8080"},  # Map container port 80 to host port 8080
            environment={"CONTAINER_TYPE": "basic"},
            rootless=True,  # Use rootless Docker if possible
            use_sudo=True,  # Use sudo as fallback
            dind_enabled=False
        )
        
        if container_info:
            logger.info(f"Container created successfully:")
            logger.info(f"  Container ID: {container_info.container_id}")
            logger.info(f"  Container Name: {container_info.container_name}")
            logger.info(f"  Image: {container_info.image}")
            logger.info(f"  Ports: {container_info.ports}")
            logger.info(f"  Status: {container_info.status}")
            
            # Check container status
            is_running, status = await manager.check_container_status(
                ssh_client=ssh_client,
                container_id=container_info.container_id,
                rootless=True,
                use_sudo=True
            )
            
            logger.info(f"Container is running: {is_running}, Status: {status}")
            
            # Setup pod-user in the container
            user_setup = await manager.setup_pod_user(
                ssh_client=ssh_client,
                container_id=container_info.container_id,
                username="pod-user",
                rootless=True,
                use_sudo=True
            )
            
            if user_setup:
                logger.info(f"User 'pod-user' set up successfully in container {container_info.container_id}")
            else:
                logger.error(f"Failed to set up 'pod-user' in container {container_info.container_id}")
            
            # Ask if we should stop and remove the container
            if input("Stop and remove container? (y/n): ").lower() == 'y':
                stopped = await manager.stop_container(
                    ssh_client=ssh_client,
                    container_id=container_info.container_id,
                    rootless=True,
                    use_sudo=True
                )
                
                if stopped:
                    logger.info(f"Container {container_info.container_id} stopped")
                    
                    removed = await manager.remove_container(
                        ssh_client=ssh_client,
                        container_id=container_info.container_id,
                        force=True,
                        rootless=True,
                        use_sudo=True
                    )
                    
                    if removed:
                        logger.info(f"Container {container_info.container_id} removed")
                    else:
                        logger.error(f"Failed to remove container {container_info.container_id}")
                else:
                    logger.error(f"Failed to stop container {container_info.container_id}")
        else:
            logger.error("Failed to create container")
    
    except Exception as e:
        logger.error(f"Error creating container: {str(e)}", exc_info=True)
    finally:
        ssh_client.close()
        logger.info("SSH connection closed")

async def create_gpu_container(
    host: str, 
    username: str, 
    password: str = None, 
    key_path: str = None, 
    image: str = "nvidia/cuda:11.7.1-base-ubuntu22.04"
):
    """
    Create a container with GPU support if available.
    
    Args:
        host: SSH host to connect to
        username: SSH username
        password: SSH password (optional)
        key_path: Path to SSH private key file (optional)
        image: Docker image to use
    """
    logger.info(f"Creating GPU container on {host} using image {image}")
    
    # Setup SSH connection
    ssh_client = SSHClient()
    ssh_client.set_missing_host_key_policy(AutoAddPolicy())
    
    try:
        if key_path:
            ssh_client.connect(host, username=username, key_filename=key_path)
        else:
            ssh_client.connect(host, username=username, password=password)
            
        logger.info(f"Connected to {host} as {username}")
        
        # Create container manager
        manager = ContainerManager()
        
        # Create a container with GPU support
        container_info = await manager.create_container(
            ssh_client=ssh_client,
            image=image,
            ports={"8000": "8000"},
            environment={"CONTAINER_TYPE": "gpu"},
            enable_gpu=True,  # Enable GPU support if available
            memory_limit="4g",
            cpu_limit="2",
            rootless=True,
            use_sudo=True
        )
        
        if container_info:
            logger.info(f"Container created successfully:")
            logger.info(f"  Container ID: {container_info.container_id}")
            logger.info(f"  Container Name: {container_info.container_name}")
            logger.info(f"  Image: {container_info.image}")
            logger.info(f"  Ports: {container_info.ports}")
            logger.info(f"  GPU Enabled: {container_info.gpu_enabled}")
            
            if container_info.gpu_enabled:
                logger.info(f"  GPU Count: {container_info.gpu_count}")
                logger.info(f"  GPU Type: {container_info.gpu_type}")
                
            # Execute a command to verify GPU access
            if container_info.gpu_enabled:
                exec_cmd = "nvidia-smi"
                logger.info(f"Executing '{exec_cmd}' in container to verify GPU access")
                
                if True:  # Using rootless Docker
                    exit_status, output, error = await manager.RootlessDockerSetup.run_docker_command(
                        ssh_client=ssh_client,
                        command=f"exec {container_info.container_id} {exec_cmd}",
                        use_sudo=True
                    )
                else:
                    exec_full_cmd = f"docker exec {container_info.container_id} {exec_cmd}"
                    # Use sudo if needed
                    if True:  # Using sudo
                        exec_full_cmd = f"sudo {exec_full_cmd}"
                    
                    stdin, stdout, stderr = ssh_client.exec_command(exec_full_cmd)
                    exit_status = stdout.channel.recv_exit_status()
                    output = stdout.read().decode().strip()
                    error = stderr.read().decode().strip()
                
                if exit_status == 0:
                    logger.info(f"GPU check successful:")
                    logger.info(output[:500] + ("..." if len(output) > 500 else ""))
                else:
                    logger.error(f"GPU check failed: {error}")
            
            # Ask if we should stop and remove the container
            if input("Stop and remove container? (y/n): ").lower() == 'y':
                stopped = await manager.stop_container(
                    ssh_client=ssh_client,
                    container_id=container_info.container_id,
                    rootless=True,
                    use_sudo=True
                )
                
                if stopped:
                    logger.info(f"Container {container_info.container_id} stopped")
                    
                    removed = await manager.remove_container(
                        ssh_client=ssh_client,
                        container_id=container_info.container_id,
                        force=True,
                        rootless=True,
                        use_sudo=True
                    )
                    
                    if removed:
                        logger.info(f"Container {container_info.container_id} removed")
                    else:
                        logger.error(f"Failed to remove container {container_info.container_id}")
                else:
                    logger.error(f"Failed to stop container {container_info.container_id}")
        else:
            logger.error("Failed to create container")
    
    except Exception as e:
        logger.error(f"Error creating container: {str(e)}", exc_info=True)
    finally:
        ssh_client.close()
        logger.info("SSH connection closed")

async def create_dind_container(
    host: str, 
    username: str, 
    password: str = None, 
    key_path: str = None, 
    image: str = "docker:dind"
):
    """
    Create a Docker-in-Docker container.
    
    Args:
        host: SSH host to connect to
        username: SSH username
        password: SSH password (optional)
        key_path: Path to SSH private key file (optional)
        image: Docker image to use
    """
    logger.info(f"Creating Docker-in-Docker container on {host} using image {image}")
    
    # Setup SSH connection
    ssh_client = SSHClient()
    ssh_client.set_missing_host_key_policy(AutoAddPolicy())
    
    try:
        if key_path:
            ssh_client.connect(host, username=username, key_filename=key_path)
        else:
            ssh_client.connect(host, username=username, password=password)
            
        logger.info(f"Connected to {host} as {username}")
        
        # Create container manager
        manager = ContainerManager()
        
        # Create a Docker-in-Docker container
        container_info = await manager.create_container(
            ssh_client=ssh_client,
            image=image,
            ports={"2375": "2375"},
            volumes={"/var/lib/docker-dind": "/var/lib/docker"},
            environment={"DOCKER_TLS_CERTDIR": ""},
            dind_enabled=True,  # Enable Docker-in-Docker
            rootless=True,
            use_sudo=True
        )
        
        if container_info:
            logger.info(f"DinD container created successfully:")
            logger.info(f"  Container ID: {container_info.container_id}")
            logger.info(f"  Container Name: {container_info.container_name}")
            logger.info(f"  Image: {container_info.image}")
            logger.info(f"  Ports: {container_info.ports}")
            
            # Setup pod-user in the container
            user_setup = await manager.setup_pod_user(
                ssh_client=ssh_client,
                container_id=container_info.container_id,
                username="pod-user",
                rootless=True,
                use_sudo=True
            )
            
            if user_setup:
                logger.info(f"User 'pod-user' set up successfully in container {container_info.container_id}")
                
                # Test Docker access within container
                logger.info("Testing Docker access within container")
                exec_cmd = "docker ps"
                
                if True:  # Using rootless Docker
                    exit_status, output, error = await manager.RootlessDockerSetup.run_docker_command(
                        ssh_client=ssh_client,
                        command=f"exec {container_info.container_id} {exec_cmd}",
                        use_sudo=True
                    )
                else:
                    exec_full_cmd = f"docker exec {container_info.container_id} {exec_cmd}"
                    # Use sudo if needed
                    if True:  # Using sudo
                        exec_full_cmd = f"sudo {exec_full_cmd}"
                    
                    stdin, stdout, stderr = ssh_client.exec_command(exec_full_cmd)
                    exit_status = stdout.channel.recv_exit_status()
                    output = stdout.read().decode().strip()
                    error = stderr.read().decode().strip()
                
                if exit_status == 0:
                    logger.info(f"Docker access test successful:")
                    logger.info(output)
                else:
                    logger.error(f"Docker access test failed: {error}")
            else:
                logger.error(f"Failed to set up 'pod-user' in container {container_info.container_id}")
            
            # Ask if we should stop and remove the container
            if input("Stop and remove container? (y/n): ").lower() == 'y':
                stopped = await manager.stop_container(
                    ssh_client=ssh_client,
                    container_id=container_info.container_id,
                    rootless=True,
                    use_sudo=True
                )
                
                if stopped:
                    logger.info(f"Container {container_info.container_id} stopped")
                    
                    removed = await manager.remove_container(
                        ssh_client=ssh_client,
                        container_id=container_info.container_id,
                        force=True,
                        rootless=True,
                        use_sudo=True
                    )
                    
                    if removed:
                        logger.info(f"Container {container_info.container_id} removed")
                    else:
                        logger.error(f"Failed to remove container {container_info.container_id}")
                else:
                    logger.error(f"Failed to stop container {container_info.container_id}")
        else:
            logger.error("Failed to create container")
    
    except Exception as e:
        logger.error(f"Error creating container: {str(e)}", exc_info=True)
    finally:
        ssh_client.close()
        logger.info("SSH connection closed")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Container Manager Example")
    parser.add_argument("--host", required=True, help="SSH host to connect to")
    parser.add_argument("--username", required=True, help="SSH username")
    parser.add_argument("--password", help="SSH password")
    parser.add_argument("--key", help="Path to SSH private key file")
    parser.add_argument("--type", choices=["basic", "gpu", "dind"], default="basic", help="Type of container to create")
    parser.add_argument("--image", help="Docker image to use")
    
    args = parser.parse_args()
    
    if not args.password and not args.key:
        parser.error("Either --password or --key must be provided")
    
    if args.type == "basic":
        image = args.image or "ubuntu:latest"
        asyncio.run(create_basic_container(args.host, args.username, args.password, args.key, image))
    elif args.type == "gpu":
        image = args.image or "nvidia/cuda:11.7.1-base-ubuntu22.04"
        asyncio.run(create_gpu_container(args.host, args.username, args.password, args.key, image))
    elif args.type == "dind":
        image = args.image or "docker:dind"
        asyncio.run(create_dind_container(args.host, args.username, args.password, args.key, image)) 