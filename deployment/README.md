# deployment/ — Raspberry Pi Deployment

Scripts and configuration files for deploying the drone onboard software to Raspberry Pi 4 and Pi 5.

## Structure

```
deployment/
├── pi4/    Deployment configs and scripts for Raspberry Pi 4
└── pi5/    Deployment configs and scripts for Raspberry Pi 5
```

## Deploying

```bash
# Copy to Pi over SSH
rsync -av deployment/pi5/ pi@raspberrypi.local:~/rescueswarm/

# Or use the install script
ssh pi@raspberrypi.local 'bash ~/rescueswarm/install.sh'
```
