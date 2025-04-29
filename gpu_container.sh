#!/bin/bash

num_gpus=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l)

# Process the first argument (image name) to ensure it has a registry prefix
if [[ $1 != *"."*"/"* ]]; then
    # Check if the image has a namespace (contains a slash)
    if [[ $1 == *"/"* ]]; then
        # Image has namespace but no registry, add docker.io/
        IMAGE_NAME="docker.io/$1"
    else
        # Image has no namespace, add docker.io/library/
        IMAGE_NAME="docker.io/library/$1"
    fi
    shift
    set -- "$IMAGE_NAME" "$@"
fi

if [ "$num_gpus" -gt 0 ]; then
    echo "GPUs are available, attempting to assign to container"
    # Try running with GPUs
    if sudo docker run --gpus all "$@"; then
        echo "Container launched successfully with GPU support."
    else
        echo "Failed to launch container with GPU support, retrying without GPUs."
        sudo docker run "$@"
    fi
else
    echo "No GPUs available or nvidia-smi not found, running container without GPUs"
    sudo docker run "$@"
fi