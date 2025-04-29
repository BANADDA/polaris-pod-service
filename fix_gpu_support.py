#!/usr/bin/env python3
"""
Fix GPU support for Podman by creating and applying required patches
"""
import logging
import os
import sys
import subprocess

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s'
)
logger = logging.getLogger("gpu_fix")

def main():
    """Main function to apply the GPU fix"""
    logger.info("Starting GPU support fix for Podman")
    
    # Check if we're using Podman
    try:
        result = subprocess.run(["docker", "--version"], capture_output=True, text=True)
        is_podman = 'podman' in result.stdout.lower()
        if not is_podman:
            logger.info("Not using Podman, no need to apply fix")
            return 0
    except Exception as e:
        logger.error(f"Failed to check Docker/Podman version: {e}")
        return 1
    
    # First import and run the patch creator
    try:
        from podman_gpu_fix import patch_container_manager
        success = patch_container_manager()
        if not success:
            logger.error("Failed to create Podman GPU patch")
            return 1
    except ImportError:
        logger.error("Could not import podman_gpu_fix module. Make sure it exists in the current directory.")
        return 1
    
    # Now apply the patch
    try:
        apply_patch_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apply_podman_patch.py")
        if not os.path.exists(apply_patch_script):
            logger.error(f"Patch script not found at {apply_patch_script}")
            return 1
        
        logger.info(f"Applying patch with {apply_patch_script}")
        result = subprocess.run([sys.executable, apply_patch_script], capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info("Patch applied successfully!")
            logger.info(result.stdout)
        else:
            logger.error(f"Failed to apply patch: {result.stderr}")
            return 1
    except Exception as e:
        logger.error(f"Error applying patch: {e}")
        return 1
    
    logger.info("GPU support fix completed successfully.")
    logger.info("Run your container with: python example_usage.py --type gpu")
    return 0

if __name__ == "__main__":
    sys.exit(main()) 