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

## Usage (`usage.py`)

The `usage.py` script demonstrates how to use the `ContainerManager` to create containers locally or remotely.

**Example (Local GPU Container):**

To run a local container with GPU support using a specific Docker image (e.g., `nvidia/cuda:11.7.1-base-ubuntu22.04`), use the following command:

```bash
python usage.py --local --type gpu-docker --image nvidia/cuda:11.7.1-base-ubuntu22.04
```

**Flags:**

*   `--local`: Run the container on the local machine instead of a remote SSH host.
*   `--type`: Specifies the type of container environment.
    *   `gpu-docker`: Creates a container with NVIDIA GPU access and Docker-in-Docker capabilities.
    *   `rootless-docker`: Creates a container using rootless Docker.
    *   `base`: Creates a basic container without special Docker configurations.
*   `--image`: (Optional) Specify the Docker image to use. Defaults may apply if not provided.

**Output:**

The script will output logs detailing the process, including GPU detection, container creation, and setup steps. Upon successful creation, it will provide the container ID, name, port mappings, and the command to access the container shell:

```bash
# Example output log message
[INFO] - example_usage - [Local] To access the container: docker exec -it <container_id> bash
``` 