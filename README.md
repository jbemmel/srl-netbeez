# srl-netbeez
Sample setup to run NetBeez Docker container agent on SR Linux

# Prepare container & run demo
Before you start, make sure that /etc/resolv.conf contains some sane DNS servers for your containers.

```
make build
sudo containerlab deploy -t srl-leafspine.lab
```
