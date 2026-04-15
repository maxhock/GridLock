#!/usr/bin/env python3
"""
Fix docker-compose.yaml commands for house_player federates.

The composegen generates services but doesn't populate all required command args.
For layout-based grids with "fill" placement, each load service needs:
  --scenario <name>
  --federate_name <federate_name_derived_from_bus>
  --timeseries <path_to_csv>

The federate name pattern for layout grids should match what composegen creates.
Looking at load.py, for non-location grids it just uses the simple federate ID.
"""

import re
from pathlib import Path


def main():
    compose_path = Path(__file__).parent / "generated" / "docker-compose.yaml"
    
    if not compose_path.exists():
        print(f"Error: {compose_path} not found")
        return
    
    with open(compose_path, "r") as f:
        content = f.read()
    
    # For layout-based grids, the federates don't have complex names
    # We need to check what the actual names should be from CST config
    # For now, use the pattern: lvgrid.loadhouse_0_bus_X
    
    scenario_name = "TestGridScenario"
    timeseries_path = "/data/input/sample_house.csv"
    
    # Track which services we fix
    fixed_count = 0
    
    # Find all house_player services and fix their commands
    # Pattern: service_name with image: "house_player"
    services = re.finditer(
        r'  (lvgrid_bus_\d+):\n(.*?)\n    image: "house_player"(.*?)(?=\n  [a-z]|\nnetworks:)',
        content,
        re.DOTALL
    )
    
    for match in services:
        service_name = match.group(1)
        prefix = match.group(0).split('command:')[0]
        
        # Extract bus number if present
        bus_match = re.search(r'lvgrid_bus_(\d+)', service_name)
        if bus_match:
            bus_id = bus_match.group(1)
            # For layout grids with fill placement, derive a unique federate name
            # CST should name them: lvgrid.loadhouse_0 for the first placement definition
            # But since we have "fill", each bus gets one: lvgrid.loadhouse_0_bus_X
            # Actually, looking at load.py for non-location grids, it seems they're just generic
            # Let's use the service name pattern as the federate name
            federate_name = service_name
            
            new_cmd = (
                f'command: /bin/bash -c "python3 main.py '
                f'--scenario {scenario_name} '
                f'--federate_name {federate_name} '
                f'--timeseries {timeseries_path}"'
            )
            
            # Find the old command in this service block and replace it
            old_block = match.group(0)
            # Extract everything up to the command line
            service_start = old_block.split('command:')[0]
            
            # Replace the entire command line in this block
            new_block = service_start + new_cmd
            # Append the rest after command (networks, etc) if any
            if 'networks:' in old_block or 'volumes:' in old_block:
                rest_match = re.search(r'\n    (networks:|volumes:.*?)(?=\n  [a-z]|\nnetworks:)', old_block, re.DOTALL)
                if rest_match:
                    new_block += '\n    ' + old_block.split('command:')[1].split('\n    networks:')[0].strip()
                    # Get the rest back
                    rest_of_service = old_block[old_block.find('\n    networks:') if 'networks:' in old_block else old_block.find('\n    volumes:'):]
                    new_block += rest_of_service
            
            content = content.replace(old_block, new_block)
            print(f"Fixed {service_name}: --scenario, --federate_name, --timeseries")
            fixed_count += 1
    
    if fixed_count == 0:
        print("No services found to fix. Using direct string replacement...")
        # Try a different approach - direct pattern matching for each service
        for i in [3, 5, 7, 9, 11, 13, 15, 17]:
            old_pattern = f'  lvgrid_bus_{i}:\n    image: "house_player"(.*?)command: /bin/bash -c "python3 main.py"'
            
            if re.search(old_pattern, content, re.DOTALL):
                new_pattern = (
                    f'  lvgrid_bus_{i}:\n    image: "house_player"'
                    f'\\1command: /bin/bash -c "python3 main.py '
                    f'--scenario {scenario_name} '
                    f'--federate_name lvgrid_bus_{i} '
                    f'--timeseries {timeseries_path}"'
                )
                content = re.sub(old_pattern, new_pattern, content, flags=re.DOTALL)
                print(f"Fixed lvgrid_bus_{i}")
                fixed_count += 1
    
    with open(compose_path, "w") as f:
        f.write(content)
    
    print(f"\nFixed {fixed_count} services. Updated {compose_path}")


if __name__ == "__main__":
    main()
