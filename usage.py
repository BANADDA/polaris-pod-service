"""
Simplified usage script to run pre-built CPU or GPU containers locally.
"""
import argparse
import subprocess
import sys
import logging
import shlex

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s'
)
logger = logging.getLogger("usage_simplified")

def run_command(command):
    """Runs a shell command and returns its output or raises an exception."""
    logger.info(f"Executing: {' '.join(command)}")
    try:
        cmd_str = ' '.join(map(shlex.quote, command))
        result = subprocess.run(cmd_str, shell=True, check=True, capture_output=True, text=True)
        logger.debug(f"Command stdout: {result.stdout}")
        if result.stderr:
            logger.warning(f"Command stderr: {result.stderr}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}")
        logger.error(f"Stderr: {e.stderr}")
        logger.error(f"Stdout: {e.stdout}")
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred running command: {e}")
        raise

def main():
    parser = argparse.ArgumentParser(
        description="Run pre-built Polaris Pod containers (CPU or GPU).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "--type",
        choices=["cpu", "gpu"],
        required=True,
        help="Specify whether to run the CPU or GPU container."
    )

    parser.add_argument(
        "-v", "--volume",
        action='append',
        metavar='HOST_PATH:CONTAINER_PATH[:MODE]',
        help="Mount a volume (e.g., /path/on/host:/path/in/container:ro). Can be used multiple times."
    )
    parser.add_argument(
        "-p", "--port",
        action='append',
        metavar='HOST_PORT:CONTAINER_PORT[/PROTOCOL]',
        help="Publish a container's port(s) to the host (e.g., 8080:80). Can be used multiple times."
    )
    parser.add_argument(
        "--name",
        help="Assign a name to the container."
    )
    parser.add_argument(
        "--add-host",
        action='append',
        metavar='HOST:IP',
        help="Add a custom host-to-IP mapping (--add-host host:ip). Can be used multiple times."
    )
    parser.add_argument(
        "--env",
        action='append',
        metavar='VAR=VALUE',
        help="Set environment variables (-e VAR=VALUE). Can be used multiple times."
    )

    args = parser.parse_args()

    docker_cmd = ["docker", "run", "-d", "--privileged"]

    image_name = f"mubarakb1999/polaris-pod:{args.type}"
    logger.info(f"Selected image: {image_name}")

    # --- Pull the latest image --- 
    try:
        logger.info(f"Attempting to pull latest image: {image_name}")
        # We can reuse the run_command function
        run_command(["docker", "pull", image_name])
        logger.info(f"Successfully pulled or verified image: {image_name}")
    except Exception as e:
        logger.error(f"Failed to pull image {image_name}: {e}")
        logger.warning("Proceeding with potentially cached image if available.")
        # Decide if you want to exit here or allow running with cached image
        # sys.exit(1) # Uncomment to exit if pull fails

    if args.type == "gpu":
        docker_cmd.extend(["--gpus", "all"])

    if args.name:
        docker_cmd.extend(["--name", args.name])
        assigned_name = args.name
    else:
        import time
        default_name = f"pod-{args.type}-{int(time.time())}"
        docker_cmd.extend(["--name", default_name])
        logger.info(f"Assigning default name: {default_name}")
        assigned_name = default_name

    if args.volume:
        for vol in args.volume:
            docker_cmd.extend(["-v", vol])

    if args.port:
        for p in args.port:
            docker_cmd.extend(["-p", p])

    if args.add_host:
        for host_entry in args.add_host:
            docker_cmd.extend(["--add-host", host_entry])

    if args.env:
        for env_var in args.env:
            docker_cmd.extend(["--env", env_var])

    docker_cmd.append(image_name)

    docker_cmd.extend(["tail", "-f", "/dev/null"])

    try:
        container_id = run_command(docker_cmd)
        logger.info(f"Container started successfully!")
        logger.info(f"Container ID: {container_id[:12]}...")
        logger.info(f"Container Name: {assigned_name}")
        logger.info(f"To access the container shell: docker exec -it {assigned_name} bash")

    except Exception as e:
        logger.error(f"Failed to start container: {e}")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Execution interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"An unhandled exception occurred: {e}", exc_info=True)
        sys.exit(1) 