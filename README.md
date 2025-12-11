# Lab-Stream-Layer-LSL-Compatability
Lab Stream Layer (LSL) Compatability


To stream to the forked OpenBCI Gui we first need to have th board stream lsl

First install dependancies with terminal via:

1. pip install pyserial numpy pylsl



   



3. run python cerelog_lsl.py in terminal:

 
 ## Note if error:  


 
   
Windows & Linux Users: If you get an error you might need to install LSL 

macOS Users (M1/M2/M3 Silicon & Intel):
macOS security often blocks the LSL driver. If you see an error saying RuntimeError: LSL binary library file was not found, follow these steps:

Install Homebrew (if you haven't already) by visiting brew.sh.

Install the LSL library via Homebrew:

Run these one at a time


brew tap labstreaminglayer/tap

brew install lsl
