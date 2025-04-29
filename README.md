 # Container Manager for GPU and Rootless Docker Support

This module provides a comprehensive solution for creating and managing Docker containers on remote machines, with robust support for:

1.  **GPU Detection & Passthrough**: Automatically detects NVIDIA GPUs and enables them for containers when available.
2.  **Rootless Docker Operation**: Sets up and uses rootless Docker to avoid sudo permission issues.
3.  **Docker-in-Docker (DinD)**: Supports running Docker inside containers.
4.  **Smart Port Mapping**: Handles port mappings.
5.  **User Setup**: Creates a non-root user (`pod-user`) with passwordless sudo and necessary package installations (including `mosh`) inside the container.

## Key Components

-   **`ContainerManager`**: Main class for creating and managing containers.
-   **`GPUDetector`**: Detects and sets up NVIDIA GPU support.
-   **`RootlessDockerSetup`**: Handles rootless Docker setup and operation.

## Usage Examples

### Basic Container Creation

```python
import asyncio
from paramiko import SSHClient, AutoAddPolicy
from src.miners.container_manager.container_manager import ContainerManager

async def create_basic_container():
    # Setup SSH connection
    ssh_client = SSHClient()
    ssh_client.set_missing_host_key_policy(AutoAddPolicy())
    ssh_client.connect('your_host', username='your_username', password='your_password')
    
    # Create container manager
    manager = ContainerManager()
    
    # Create a basic container
    container_info = await manager.create_container(
        ssh_client=ssh_client,
        image="ubuntu:latest",
        ports={"80": "8080"},  # Map container port 80 to host port 8080
        rootless=True          # Use rootless Docker if possible
    )
    
    if container_info:
        print(f"Container created: {container_info.container_id}")
        print(f"Container ports: {container_info.ports}")

        # Setup the user inside the container
        await manager.setup_pod_user(
            ssh_client=ssh_client,
            container_id=container_info.container_id,
            rootless=True
        )
    else:
        print("Failed to create container")

    ssh_client.close()

# Run the example
# asyncio.run(create_basic_container())
```

### Creating a GPU-Enabled Container

```python
async def create_gpu_container():
    # Setup SSH connection
    ssh_client = SSHClient()
    ssh_client.set_missing_host_key_policy(AutoAddPolicy())
    ssh_client.connect('your_host', username='your_username', password='your_password')
    
    # Create container manager
    manager = ContainerManager()
    
    # Create a container with GPU support
    container_info = await manager.create_container(
        ssh_client=ssh_client,
        image="nvidia/cuda:11.7.1-base-ubuntu22.04",
        enable_gpu=True,  # Enable GPU support if available
        ports={"8000": "8000"},
        memory_limit="4g",
        cpu_limit="2",
        rootless=True, # Try rootless first
        use_sudo=True  # Allow fallback to sudo
    )
    
    if container_info:
        print(f"Container created with GPU support: {container_info.gpu_enabled}")
        if container_info.gpu_enabled:
            print(f"GPU count: {container_info.gpu_count}")
            print(f"GPU type: {container_info.gpu_type}")

        # Setup the user inside the container
        await manager.setup_pod_user(
            ssh_client=ssh_client,
            container_id=container_info.container_id,
            rootless=True,
            use_sudo=True
        )
    else:
        print("Failed to create container")

    ssh_client.close()
```

### Creating a Docker-in-Docker Container

```python
async def create_dind_container():
    # Setup SSH connection
    ssh_client = SSHClient()
    ssh_client.set_missing_host_key_policy(AutoAddPolicy())
    ssh_client.connect('your_host', username='your_username', password='your_password')
    
    # Create container manager
    manager = ContainerManager()
    
    # Create a Docker-in-Docker container
    container_info = await manager.create_container(
        ssh_client=ssh_client,
        image="docker:dind",
        dind_enabled=True,  # Enable Docker-in-Docker support
        ports={"2375": "2375"},
        volumes={"/var/lib/docker-dind": "/var/lib/docker"},
        rootless=False, # DinD often needs root privileges on the host
        use_sudo=True   # Likely need sudo for DinD
    )
    
    if container_info:
        print(f"DinD container created: {container_info.container_id}")
        
        # Setup a non-root user with Docker access
        user_setup = await manager.setup_pod_user(
            ssh_client=ssh_client,
            container_id=container_info.container_id,
            username="pod-user",
            rootless=False,
            use_sudo=True
        )
        
        if user_setup:
            print("pod-user set up successfully with Docker access")
    else:
        print("Failed to create DinD container")

    ssh_client.close()
```

## Integration with Pod Allocation Service

This container manager is designed to be integrated with the existing `PodService` for allocating containers to users:

```python
# In pod_service.py (simplified example)

from src.miners.container_manager.container_manager import ContainerManager
# from src.miners.container_manager.gpu_detector import GPUDetector # Detector is used internally by manager

class PodService:
    # ... existing code ...

    async def _create_and_setup_container(self, ssh_client, pod, compute_resource, image, ssh_port, web_port):
        """Helper to create and setup container using ContainerManager."""
        
        container_manager = ContainerManager()
        container_name = f"polaris-pod-{pod.id}" # Use pod ID for name
        
        # Prepare environment variables
        environment = {"POD_ID": pod.id, "TERM": "xterm-256color"}
        
        # Prepare ports
        ports = {"22": str(ssh_port)} # Map container SSH port
        if web_port: # Add web port if applicable
            ports["80"] = str(web_port)
            
        # Create container with proper configuration
        container_info = await container_manager.create_container(
            ssh_client=ssh_client,
            image=image,
            container_name=container_name,
            ports=ports,
            # volumes={"host_data_path": "/data"}, # Example volume mount
            environment=environment,
            enable_gpu=compute_resource.resource_type == "GPU",
            cpu_limit=str(compute_resource.cpu.count) if hasattr(compute_resource, 'cpu') else None,
            memory_limit=f"{compute_resource.memory.size}{compute_resource.memory.unit}" if hasattr(compute_resource, 'memory') else None,
            use_sudo=True,  # Allow sudo fallback if needed
            dind_enabled=True,  # Enable Docker-in-Docker
            rootless=True  # Try rootless Docker first
        )
        
        if not container_info:
            raise Exception("Failed to create container using ContainerManager")
        
        # Setup pod-user in the container
        user_setup = await container_manager.setup_pod_user(
            ssh_client=ssh_client,
            container_id=container_info.container_id,
            username="pod-user",
            rootless=True, # Match rootless setting used for creation
            use_sudo=True  # Allow sudo fallback
        )
        
        if not user_setup:
            # Decide how critical user setup is - log warning or raise error?
            logger.warning(f"Failed to set up pod-user in container {container_info.container_id}, container might not function correctly.")
            # raise Exception("Failed to setup pod-user in container")
        
        return container_info

    async def create_pod(self, pod: Pod) -> PodActionResponse:
        # ... existing setup code (get compute resource, ssh client, ports) ...
        
        try:
            container_info = await self._create_and_setup_container(
                ssh_client, pod, compute_resource, image, ssh_port, web_port
            )
            
            # ... (update Pod object with container_info details) ...
            pod.container_id = container_info.container_id
            pod.name = container_info.container_name
            # ... update other pod attributes ...
            
            # Save pod state
            # await self.pod_repository.save(pod)
            
            return PodActionResponse(
                success=True,
                message="Pod created successfully",
                pod_id=pod.id,
                # pod_info=container_info.to_dict() # Optionally return detailed info
            )
            
        except Exception as e:
            logger.exception(f"Error during pod creation: {e}")
            # Add cleanup logic here (e.g., try to remove partially created container)
            return PodActionResponse(success=False, message=str(e), pod_id=pod.id)
        finally:
            if ssh_client:
                ssh_client.close()

    # ... rest of PodService ...

```

## Error Handling

The container manager includes comprehensive error handling and logging throughout, making it easy to diagnose issues that may arise during container operations.

## Automatic Recovery

The container manager can automatically:

1.  Detect and install NVIDIA GPU drivers and toolkit if needed
2.  Set up rootless Docker if it's not already configured
3.  Fall back to regular Docker with sudo if rootless setup fails
4.  Safely handle Docker command errors with detailed logging

## Prerequisites

-   Python 3.7+
-   `paramiko` library
-   Docker installed on the remote machine (or will attempt to install it)
-   For GPU support: NVIDIA GPU with compatible drivers (or will attempt to install them) 