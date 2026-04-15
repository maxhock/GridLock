#!/usr/bin/env python3
"""
Clean up the generated docker-compose.yaml by:
1. Removing old/broken lvgrid_bus_X services  
2. Keep MONGO_HOST/POSTGRES_HOST as host.docker.internal (for Docker Desktop on Mac)
3. Adding extra_hosts entries for proper DNS resolution
"""

import re
import sys
from pathlib import Path


def main():
    compose_path = Path(__file__).parent / "generated" / "docker-compose.yaml"
    
    if not compose_path.exists():
        print(f"Error: {compose_path} not found", file=sys.stderr)
        return 1
    
    with open(compose_path) as f:
        lines = f.readlines()
    
    # Step 1: Remove old lvgrid_bus_X services
    filtered_lines = []
    skip_service = False
    
    for line in lines:
        # Check if this line starts a bad service (lvgrid_bus_<num>:)
        if re.match(r'^  lvgrid_bus_\d+:', line):
            skip_service = True
            continue
        
        # If we're in a bad service, skip until we hit the next service
        if skip_service:
            if re.match(r'^(services|networks|volumes|configs|secrets):', line) or re.match(r'^  [A-Za-z0-9_.-]+:', line):
                skip_service = False
            else:
                continue
        
        filtered_lines.append(line)
    
    content = ''.join(filtered_lines)
    
    # Step 2: Revert POSTGRES_HOST/MONGO_HOST to host.docker.internal for Docker Desktop
    content = content.replace('POSTGRES_HOST: "database"', 'POSTGRES_HOST: "host.docker.internal"')
    content = content.replace('MONGO_HOST: "mongodb://mongodb"', 'MONGO_HOST: "mongodb://host.docker.internal"')
    
    # Step 3: Remove obsolete sim_net references while keeping cst_net intact
    content = re.sub(r'^\s{6}sim_net:\n', '', content, flags=re.MULTILINE)

    # Collapse accidental blank runs introduced by removals
    content = re.sub(r'\n\n+', '\n', content)
    
    # Step 4: Ensure networks section is cleaned up
    if 'sim_net:\n    external:' in content:
        networks_pattern = r'  sim_net:\n    external: true\n    name: gridlock-preflight_sim_net\n'
        content = re.sub(networks_pattern, '', content)
    
    # Write back
    with open(compose_path, 'w') as f:
        f.write(content)
    
    # Verification
    if 'lvgrid.lvgrid_bus_' not in content:
        print("Error: Failed to find correct services after cleanup", file=sys.stderr)
        return 1
    
    if re.search(r'^  lvgrid_bus_\d+:', content, re.MULTILINE):
        print("Error: Still found old lvgrid_bus_ services", file=sys.stderr)
        return 1
    
    if 'POSTGRES_HOST: "host.docker.internal"' not in content:
        print("Error: Failed to restore POSTGRES_HOST", file=sys.stderr)
        return 1
    
    if 'MONGO_HOST: "mongodb://host.docker.internal"' not in content:
        print("Error: Failed to restore MONGO_HOST", file=sys.stderr)
        return 1
    
    print("Cleaned up docker-compose.yaml successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
