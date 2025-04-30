# Polaris Pod Service - Pre-built Containers

This project provides pre-built Docker containers optimized for CPU and NVIDIA GPU workloads, simplifying the setup process.

## Features

*   **Pre-built Images:** Ready-to-use images hosted on Docker Hub (`mubarakb1999/polaris-pod`).
*   **CPU & GPU Versions:** Separate images (`:cpu`, `:gpu`) tailored for specific hardware.
*   **Standardized Environment:** Includes Ubuntu 22.04 base, common utilities (`git`, `curl`, `vim`, `mosh`, etc.), and a `pod-user` with passwordless `sudo`.
*   **Simple Launch Script:** `usage.py` script to easily run containers with common Docker options.

## Prerequisites

*   **Docker:** Docker must be installed and running on your local machine.
*   **Python:** Python 3.7+ (for running the `usage.py` script).
*   **(GPU Version Only):** NVIDIA GPU with compatible drivers installed on the host machine. The NVIDIA Container Toolkit is also recommended.

## Quick Start (`usage.py`)

The `usage.py` script pulls the appropriate image from Docker Hub and runs it using `docker run`.

**Basic Usage:**

*   **Run CPU container:**
    ```bash
    python usage.py --type cpu
    ```
*   **Run GPU container:**
    ```bash
    python usage.py --type gpu
    ```

**Important Note (Docker-in-Docker):**

These container images include a full Docker engine installation inside them (Docker-in-Docker). This requires the container to be run with elevated privileges. The `usage.py` script automatically adds the necessary `--privileged` flag to the `docker run` command.

**Common Options:**

The script passes common flags directly to `docker run`:

*   `--name <container_name>`: Assign a specific name to the container.
*   `-v /host/path:/container/path`: Mount a volume from the host into the container.
*   `-p host_port:container_port`: Map a port from the host to the container.
*   `--env VAR=value`: Set environment variables inside the container.
*   `--add-host hostname:ip`: Add custom host mappings.

**Example (GPU container with volume and port mapping):**

```bash
python usage.py --type gpu --name my-gpu-pod -v ~/my_data:/data -p 8080:80
```

**Output:**

The script will output logs, the container ID, the container name, and the command needed to access the container's shell:

```log
INFO:usage_simplified:Container started successfully!
INFO:usage_simplified:Container ID: a1b2c3d4e5f6...
INFO:usage_simplified:Container Name: my-gpu-pod
INFO:usage_simplified:To access the container shell: docker exec -it my-gpu-pod bash
```

## Building Images Locally (Optional)

If you need to customize the images, you can build them locally:

1.  Modify `Dockerfile.cpu` or `Dockerfile.gpu` as needed.
2.  Build the images:
    ```bash
    docker build -t your-tag:cpu -f Dockerfile.cpu .
    docker build -t your-tag:gpu -f Dockerfile.gpu .
    ```
3.  Update `usage.py` to use `your-tag` instead of `mubarakb1999/polaris-pod` if you want the script to use your local builds. 