#!/usr/bin/env python3
"""
Utility script for running Docker commands with automatic sudo if needed
"""
import asyncio
import logging
import os
import sys
import shlex
import subprocess
from typing import Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s')
logger = logging.getLogger("docker_sudo")

async def run_docker_cmd(command: str, try_sudo: bool = True) -> Tuple[int, str, str]:
    """
    Run a Docker command, automatically falling back to sudo if permission denied
    
    Args:
        command: Docker command to run (without the 'docker' prefix)
        try_sudo: Whether to attempt with sudo if permission denied
        
    Returns:
        Tuple of (exit_status, stdout, stderr)
    """
    # Add 'docker' prefix if not already present
    full_cmd = command if command.startswith("docker ") else f"docker {command}"
    
    # Try without sudo first
    logger.debug(f"Running command: {full_cmd}")
    try:
        process = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        exit_status = process.returncode
        
        stdout_data = stdout.decode('utf-8').strip()
        stderr_data = stderr.decode('utf-8').strip()
        
        # Check if we got permission denied and need to retry with sudo
        if exit_status != 0 and try_sudo and "permission denied" in stderr_data.lower():
            logger.info(f"Permission denied, retrying with sudo")
            sudo_cmd = f"sudo {full_cmd}"
            
            sudo_process = await asyncio.create_subprocess_shell(
                sudo_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            sudo_stdout, sudo_stderr = await sudo_process.communicate()
            sudo_exit_status = sudo_process.returncode
            
            sudo_stdout_data = sudo_stdout.decode('utf-8').strip()
            sudo_stderr_data = sudo_stderr.decode('utf-8').strip()
            
            return sudo_exit_status, sudo_stdout_data, sudo_stderr_data
        
        return exit_status, stdout_data, stderr_data
    except Exception as e:
        logger.error(f"Error executing command: {e}")
        return 1, "", str(e)

async def main():
    """Main function to demonstrate usage"""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} DOCKER_COMMAND")
        print(f"Example: {sys.argv[0]} ps -a")
        return 1
    
    # Combine all arguments as the command
    command = " ".join(sys.argv[1:])
    
    status, output, error = await run_docker_cmd(command)
    
    # Print results
    if status == 0:
        print(output)
        return 0
    else:
        print(f"Error: {error}", file=sys.stderr)
        return status

if __name__ == "__main__":
    if sys.platform == "win32":
        # Windows-specific asyncio setup
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    loop = asyncio.get_event_loop()
    try:
        sys.exit(loop.run_until_complete(main()))
    except KeyboardInterrupt:
        print("Operation interrupted", file=sys.stderr)
        sys.exit(1) 