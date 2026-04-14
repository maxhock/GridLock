#!/bin/bash
set -e


# Run all test services, force rebuild, and remove orphans
docker compose -f docker-compose.test.yml up --build --remove-orphans
TEST_RESULT=$?

# Always bring down the environment after tests to remove containers and networks
docker compose -f docker-compose.test.yml down --remove-orphans

if [ $TEST_RESULT -eq 0 ]; then
    printf '\033[1;32mAll tests passed!\033[0m\n'
else
    printf '\033[1;31mSome tests failed. See output above.\033[0m\n'
fi
exit $TEST_RESULT
