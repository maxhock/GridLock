1. mongodb volume not clean, so mongodb does not start - FIXED: deleted volume gridlock2_mongo_data
2. infdb can return 0 grids for a location, which is not found until grid federate breaks - FIXED: 
   - infdb now validates results and fails immediately with clear error message if no grids found
   - run.sh now checks infdb exit code and aborts entire execution (doesn't proceed to Stage 3)
   - Use PLZ codes that exist in pylovo.grid_result (e.g., 91074, 91099, 91359 instead of 91301)
3. cst dbs are potentially reset every run - FIXED: automatic run tracking implemented
   - Each execution creates unique scenario with timestamp (format: TestGrid_YYYYMMDD_HHMMSS)
   - Stores git commit hash, full experiment YAML, and run metadata
   - Scenarios accumulate in CST metadata store (JSON or MongoDB backend)
   - Failed runs do NOT create scenario metadata
   - Timeseries data automatically tagged with scenario name for isolation
4. when not forcing docker compose to abort when the first federate dies logger runs longer, blocking finish (Fixed, removed manual logger)
4. check if you can also use local grid data. FIXED
5. synchronise clocks and runtimes
6. Load nomenclature not correct 'lv-grid_91074_1_4/loadhouse_0/load_58/active_power'
7. unknown route for messages in last timestep of simulation (because house load is done and turns off)
8. The databases are killed after each experiment even though they should stay running and be accessible - FIXED: run.sh now only stops databases if it started them; pre-existing database containers are left running
9. Test house as well
10. what happens if the timeseries and experiment timeframe are mismatched?
11. make output less verbose