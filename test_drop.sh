#!/bin/bash
export GZ_VERSION=harmonic

rm -f output_state.txt
gz topic -e -t /rescueswarm/payload/slot_1/state > output_state.txt &
PID_STATE=$!

sleep 1
echo "Publishing detach with empty string..."
gz topic -t /rescueswarm/payload/slot_1/detach -m gz.msgs.Empty -p ''

sleep 2
echo "State after empty string:"
cat output_state.txt

echo "Publishing detach with unused: true..."
gz topic -t /rescueswarm/payload/slot_1/detach -m gz.msgs.Empty -p 'unused: true'

sleep 2
echo "State after unused: true:"
cat output_state.txt

kill $PID_STATE
