# Lab-Stream-Layer-LSL-Compatability
Lab Stream Layer (LSL) Compatability


To stream to the forked OpenBCI Gui we first need to have th board stream lsl

# STEPS

First install dependancies with terminal via:

1. pip install pyserial numpy pylsl



   



3. run python cerelog_lsl.py in terminal:


# That easy!

 
 ## Note if error:  


 
   
Windows & Linux Users: If you get an error you might need to install LSL 

macOS Users (M1/M2/M3 Silicon & Intel):
macOS security often blocks the LSL driver. If you see an error saying RuntimeError: LSL binary library file was not found, follow these steps:

Install Homebrew (if you haven't already) by visiting brew.sh.

Install the LSL library via Homebrew:

Run these one at a time


brew tap labstreaminglayer/tap

brew install lsl



##Other possible error:



The error java.lang.OutOfMemoryError: Java heap space means that the Processing application itself ran out of RAM (Memory) while trying to compile or run the OpenBCI GUI. The OpenBCI GUI is a massive program, and the default memory settings in Processing 3.5 are often too low to handle it.

How to Fix It

Open the Processing app.

Go to the Menu bar:

Mac: Processing -> Preferences

Windows: File -> Preferences

Look for the option: "Increase maximum availa
ble memory to".


Change the number to 1024 MB

Click OK.
Restart Processing (Close it completely and open it again) for the change to take effect.
