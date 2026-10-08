#!/bin/bash

script_name=`basename $0`
echo $(date -u +"%Y-%m-%d %H:%M:%S.%3NZ") - $script_name started
. constants.ini

response=$(curl -s -X GET "$CKB_RUUTER_INTERNAL/pipeline/scheduler-check-for-unscheduled-records")
echo $(date -u +"%Y-%m-%d %H:%M:%S.%3NZ") - $response

echo $(date -u +"%Y-%m-%d %H:%M:%S.%3NZ") - $script_name finished
