# Update-Over-the-Air
## Equipment 
* 2 Raspberry Pi 4, Model B with Power
* 2 Microsd Card
* Canakit MicroSD Reader
* PC with Ubuntu downloaded 
## Setup the Environment
### SD card formatter 
To start with a clean SD card, make sure to format it using the SD card formatter Tool. You can download it on your computer using this link https://www.sdcard.org/downloads/formatter/.
### Balena Etcher
Balena Etcher can be used to Flash images to SD cards and USB drives safely and easily. This tool will be used to flash the ubuntu image on the SD card. It can be downloaded using this link https://www.balena.io/etcher/
### Ubuntu 21.10 for raspberry pi
Download the Ubuntu Desktop image using this link https://ubuntu.com/download/raspberry-pi
### Flashing the SD card
After downloading all the required tools, open Balena etcher and select to flash the card using the ubuntu file you downloaded previously. 

## Installation
#### This will be done on the PC and on both Raspberry pis
First download some development libraries that are necassary to install some of Update over the air dependencies :
```
$ sudo apt-get install build-essential libssl-dev libffi-dev python-dev python3-dev
```
To download and install the Update over the air code and its dependencies, run the following:
```
$ git clone https://github.com/shamma-alblooshi/Update-Over-the-Air
$ cd Update-Over-the-Air
$ pip3 install -r dev-requirements.txt
```
## Initializing the Server 
The PC ubuntu will run the Director and Image Repo <br />
##### Note: Please make sure to change the HOSTING address in server/demo/__init__.py file , to the address of your local machine
From the root directory run the following commands:
```
$ cd server
$ python3 -i demo/start_servers.py
```
## Setting up the Fog drone
##### Note: Please make sure to change the HOSTING2 address in server/demo/__init__.py file , to the address of your raspberry pi
On one raspberry pi , run the following commands:
```
$ cd drone
$ python3
>>> import demo.demo_fogdrone as dp
>>> dp.clean_slate() # sets up a fresh Primary that has never been updated
>>> dp.update_cycle()
```
## Setting up the Edge drone
##### Note: Please make sure to change the HOSTING2 address in server/demo/__init__.py file , to the address of your raspberry pi
On the other raspberry pi , run the following commands:
```
$ cd drone
$ python3
>>> import demo.demo_edgedrone as ds
>>> ds.clean_slate() # sets up a fresh Primary that has never been updated
>>> ds.update_cycle()
```
## Deliver an Update 
To deliver an update to the fog drone and edge drone, you'll first need to add the firmware image to the image repository and assign it to the swarm of drones. <br />
Execute the following code in the PC :
```
>>> firmware_fname = filepath_in_repo = 'swarm.img'
>>> open(firmware_fname, 'w').write('Fresh firmware image')
>>> di.add_target_to_imagerepo(firmware_fname, filepath_in_repo)
>>> di.write_to_live()
```
Next, Execute the following code in the PC ( this will assign a new image to the fog drone in a specific swarm)
```
>>> suid='#name of swarm'; duid='# name of fog drone'
>>> dd.add_target_to_director(firmware_fname, filepath_in_repo, suid, duid)
>>> dd.write_to_live(suid_to_update=suid)
```
Next on the Fog drone run :
```
dp.update_cycle()
```
Finally, run this command on the Edge drone
```
ds.update_cycle()
```
