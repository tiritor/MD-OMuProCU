#!/bin/bash

SESSION_NAME="md-omuprocu"
SW1="sde-sw1"
SW2="sde-sw2"
SW3="sde-sw3"
SW4="sde-sw4"
HOST1="sde-nic1"
HOST2="sde-nic2"

DEBUG=0
START_OMUPROCU=0

POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    -d|--debug)
      DEBUG=1
      shift # past argument
      ;;
    -s|--start)
      START_OMUPROCU=1
      shift # past argument
      ;;
    -A|--all)
      DEBUG=1
      START_OMUPROCU=1
      shift # past argument
      ;;
    -a|--attach)
      tmux attach-session -t $SESSION_NAME
      exit 0
      ;;
    -k|--kill)
      echo "Killing OMuProCU..."
      tmux send-keys -t $SESSION_NAME:0.0 'C-c'
      tmux send-keys -t $SESSION_NAME:0.1 'C-c'
      tmux send-keys -t $SESSION_NAME:0.2 'C-c'
      tmux send-keys -t $SESSION_NAME:0.3 'C-c'
      tmux send-keys -t $SESSION_NAME:0.4 'C-c'
      echo "Waiting for OMuProCU instances and global view to shut down..."
      sleep 60
      tmux kill-session -t $SESSION_NAME
      echo "Session $SESSION_NAME killed."
      exit 0
      ;;
    -h|--help)
      echo "Usage: $0 [-d|--debug] [-s|--start] [-k|--kill] [-h|--help] [-a|--attach] [-A|--all]"
      exit 0
      ;;
    -*|--*)
      echo "Unknown option $1"
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1") # save positional arg
      shift # past argument
      ;;
  esac
done

set -- "${POSITIONAL_ARGS[@]}" # restore positional parameters


tmux new-session -d -s $SESSION_NAME -x "$(tput cols)" -y "$(tput lines)"

tmux rename-window -t $SESSION_NAME 'overview'
tmux select-window -t $SESSION_NAME:0
tmux split-window -v -p 60 -t $SESSION_NAME 'bash'
tmux split-window -h -p 50 -t $SESSION_NAME:0 'bash'
tmux split-window -v -p 50 -t $SESSION_NAME:0 'bash'
tmux split-window -v -p 50 -t $SESSION_NAME:0.1 'bash'
tmux select-pane -t $SESSION_NAME:0.0 -T 'md-omuprocu @ s2'
tmux select-pane -t $SESSION_NAME:0.1 -T 'omuprocu @ s1'
tmux select-pane -t $SESSION_NAME:0.2 -T 'omuprocu @ s2'
tmux select-pane -t $SESSION_NAME:0.3 -T 'omuprocu @ s3'
tmux select-pane -t $SESSION_NAME:0.4 -T 'omuprocu @ s4'

tmux new-window -t $SESSION_NAME:1 -n 'omuprocu-client'
tmux select-pane -t $SESSION_NAME:1 -T 'omuprocu-client @ s2'



if [ $DEBUG -eq 1 ] && [ $START_OMUPROCU -eq 0 ]; then 
    echo "Debug mode. OMuProCU will not be started."
    tmux send-keys -t $SESSION_NAME:0.0 "ssh -t $SW2" 'c-m' 'cd working_space/md-omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.1 "ssh -t $SW1" 'c-m' 'cd working_space/omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.2 "ssh -t $SW2" 'c-m' 'cd working_space/omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.3 "ssh -t $SW3" 'c-m' 'cd working_space/omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.4 "ssh -t $SW4" 'c-m' 'cd working_space/omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:1 "ssh -t $SW2" 'c-m' 'cd working_space/md-omuprocu' 'c-m' 'source .venv/bin/activate' 'c-m' 'python3 moc_shell'
    
elif [ $DEBUG -eq 0 ] && [ $START_OMUPROCU -eq 1 ]; then 
    echo "Standalone mode. OMuProCU will be started."
    tmux send-keys -t $SESSION_NAME:0.0 "ssh -t $SW2" 'c-m' 'cd working_space/md-omuprocu' 'c-m' './start-mdomuprocu.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.1 "ssh -t $SW1" 'c-m' 'cd working_space/omuprocu' 'c-m' './start-orchestrator.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.2 "ssh -t $SW2" 'c-m' 'cd working_space/omuprocu' 'c-m' './start-orchestrator.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.3 "ssh -t $SW3" 'c-m' 'cd working_space/omuprocu' 'c-m' './start-orchestrator.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.4 "ssh -t $SW4" 'c-m' 'cd working_space/omuprocu' 'c-m' './start-orchestrator.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:1 "ssh -t $SW2" 'c-m' 'cd working_space/md-omuprocu' 'c-m' 'source .venv/bin/activate' 'c-m' 'python3 moc_shell' 'c-m'
elif [ $DEBUG -eq 1 ] && [ $START_OMUPROCU -eq 1 ]; then 
    echo "Standalone mode. OMuProCU will be started in debug mode."
    tmux send-keys -t $SESSION_NAME:0.0 "ssh -t $SW2" 'c-m' 'cd working_space/md-omuprocu' 'c-m' './start-mdomuprocu.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.1 "ssh -t $SW1" 'c-m' 'cd working_space/omuprocu' 'c-m' './start-orchestrator.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.2 "ssh -t $SW2" 'c-m' 'cd working_space/omuprocu' 'c-m' './start-orchestrator.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.3 "ssh -t $SW3" 'c-m' 'cd working_space/omuprocu' 'c-m' './start-orchestrator.sh' 'c-m'
    tmux send-keys -t $SESSION_NAME:0.4 "ssh -t $SW4" 'c-m' 'cd working_space/omuprocu' 'c-m' './start-orchestrator.sh' 'c-m'

    tmux send-keys -t $SESSION_NAME:1 "ssh -t $SW2" 'c-m' 'cd working_space/md-omuprocu' 'c-m' 'source .venv/bin/activate' 'c-m' 'python3 moc_shell' 'c-m'

    tmux new-window -t $SESSION_NAME:2 -n 'debug'
    tmux split-window -v -l 60% -t $SESSION_NAME:2 'bash'
    tmux split-window -h -l 50% -t $SESSION_NAME:2 'bash'
    tmux split-window -v -l 50% -t $SESSION_NAME:2 'bash'
    tmux split-window -v -l 50% -t $SESSION_NAME:2.1 'bash'
    tmux select-pane -t $SESSION_NAME:2.0 -T 'md-omuprocu @ s2'
    tmux select-pane -t $SESSION_NAME:2.1 -T 'omuprocu @ s1'
    tmux select-pane -t $SESSION_NAME:2.2 -T 'omuprocu @ s2'
    tmux select-pane -t $SESSION_NAME:2.3 -T 'omuprocu @ s3'
    tmux select-pane -t $SESSION_NAME:2.4 -T 'omuprocu @ s4'

    tmux send-keys -t $SESSION_NAME:2.0 "ssh -t $SW2" 'c-m' 'cd working_space/md-omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:2.1 "ssh -t $SW1" 'c-m' 'cd working_space/omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:2.2 "ssh -t $SW2" 'c-m' 'cd working_space/omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:2.3 "ssh -t $SW3" 'c-m' 'cd working_space/omuprocu' 'c-m'
    tmux send-keys -t $SESSION_NAME:2.4 "ssh -t $SW4" 'c-m' 'cd working_space/omuprocu' 'c-m'

    tmux new-window -t $SESSION_NAME:3 -n 'monitoring'
    tmux split-window -v -l 60% -t $SESSION_NAME:3 'bash'
    tmux split-window -h -l 50% -t $SESSION_NAME:3 'bash'
    tmux split-window -v -l 50% -t $SESSION_NAME:3 'bash'
    tmux split-window -v -l 50% -t $SESSION_NAME:3.1 'bash'
    tmux select-pane -t $SESSION_NAME:3.0 -T 'md-omuprocu @ s2'
    tmux select-pane -t $SESSION_NAME:3.1 -T 'omuprocu @ s1'
    tmux select-pane -t $SESSION_NAME:3.2 -T 'omuprocu @ s2'
    tmux select-pane -t $SESSION_NAME:3.3 -T 'omuprocu @ s3'
    tmux select-pane -t $SESSION_NAME:3.4 -T 'omuprocu @ s4'

    tmux send-keys -t $SESSION_NAME:3.0 "ssh -t $SW2" 'c-m' 'cd working_space/md-omuprocu' 'c-m' 'htop' 'c-m'
    tmux send-keys -t $SESSION_NAME:3.1 "ssh -t $SW1" 'c-m' 'cd working_space/omuprocu' 'c-m' 'htop' 'c-m'
    tmux send-keys -t $SESSION_NAME:3.2 "ssh -t $SW2" 'c-m' 'cd working_space/omuprocu' 'c-m' 'htop' 'c-m'
    tmux send-keys -t $SESSION_NAME:3.3 "ssh -t $SW3" 'c-m' 'cd working_space/omuprocu' 'c-m' 'htop' 'c-m'
    tmux send-keys -t $SESSION_NAME:3.4 "ssh -t $SW4" 'c-m' 'cd working_space/omuprocu' 'c-m' 'htop' 'c-m'
    
    tmux new-window -t $SESSION_NAME:4 -n 'Hosts'
    tmux split-window -v -l 50% -t $SESSION_NAME:4 'bash'
    tmux select-pane -t $SESSION_NAME:4.0 "ssh -t $HOSTS1" 'c-m'
    tmux select-pane -t $SESSION_NAME:4.1 "ssh -t $HOSTS2" 'c-m'
else
    echo "No mode selected. Please select either debug or standalone mode."
    exit 1
    tmux kill-session -t $SESSION_NAME
fi

tmux select-pane -t $SESSION_NAME:0.0
tmux select-window -t $SESSION_NAME:0

tmux attach-session -t $SESSION_NAME
