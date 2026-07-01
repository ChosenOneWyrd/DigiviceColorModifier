# Digivice Color Modifier
Tool for Digivice 25th color evolution modding and D-3 25th color evolution modding 

## LINKS

### Discord Servers (Highly Recommended to Join)
- [The CIM/CBM Community](https://discord.com/invite/x9FeKPsMdr)
- [Digitama Hatchery](https://discord.com/invite/digimon)

### Full Download Link (with tutorials, required software, etc)
- [Download Folder](https://drive.google.com/drive/folders/1HYOpG9URBFviwJ7M_MmHEK0l_AJfyFz5?usp=sharing)

### Source Code Repo
- [GitHub Repo](https://github.com/ChosenOneWyrd/DigiviceColorModifier)

### Which digimon can be added? Any for which spritesheets exist! 
- [Vital Bracelet BE Digimon List](https://humulos.com/digimon/vbbe/)
- [Vital Bracelet DM Digimon List](https://humulos.com/digimon/vbdm/)
- [Ranked Vital Bracelet BE Digimon](https://humulos.com/digimon/vbbe/ranked/)
- [Ranked Vital Bracelet DM Digimon](https://humulos.com/digimon/vbdm/ranked/)

## Where to Get Sprites of Other Digimon
- [Sirec DIM Archive (Reddit)](https://www.reddit.com/r/DigimonVitalBracelet/comments/1c2xm2y/sirec_dim_archive/) You can download the above and use the **DIM-Modifier-Tool.exe** in my DigiviceColorModifier.zip to open the above files and export their spritesheets.
- [Google Drive Folder 1](https://drive.google.com/drive/folders/1Nh4v0p_xISOuqV755uPW4MSNBywu7h2E)
- [Google Drive Folder 2](https://drive.google.com/drive/folders/13OfTj8YD8vEZjAgZm8zlPpyYf2UESWKO?usp=sharing)

---

## HARDWARE REQUIRED
1. CH341A flash programmer: https://www.amazon.com/dp/B0BYSRSZPC ,
2. PCB Clip 7P Single 1.5mm with Dupont: https://www.aliexpress.us/item/3256806423435336.html 
3. A screw driver of the right size. Don't attempt with a screw driver too big or too small, get a size close to the screws on the back of the D-3 or Digivice.

---

## FEATURES

### Currently Supported:
1. Replacing D-3 25th color and Digivice 25th color Sprites & Images
2. Replacing D-3 25th color and Digivice 25th color Digimon Names, baby digimon names, digimental names, map names
3. Replacing D-3 25th color and Digivice 25th color Digimon Power
4. Replacing D-3 25th color and Digivice 25th color Digimon Stage
5. Replacing D-3 25th color and Digivice 25th color sounds (BGM + voices)
6. Swapping or Copying evolution_animations, sprite_indexes, names, attack_shot_sprite_indexes, attack_voices, attack_shot_sounds for D-3 25th color and Digivice 25th color.
7. Modding steps, enemy_stage, minimum_stage_to_battle for Map Battles for both D-3 25th color and Digivice 25th color.
8. Modding battle_type Follow, Shake or Mash and the count of Follow, Shake or Mash to get a hit for both D-3 25th color and Digivice 25th color.
9. Converting all Friend or Event encounters into actual battles  for both D-3 25th color and Digivice 25th color.
10. Moving evolution slots from one digimon to another D-3 ONLY.
11. Playing digital_gate_open animation for all maps, D-3 ONLY
12. Modding boss_cut_scene_id (partial support) D-3 ONLY.
13. Adding new evolution slots for D-3 ONLY (partial support).
14. Support for both Mac and Windows.

### Unsupported:
- Adding new slots for digimon, evolution lines, etc will be forever unsupported.


### Planned for Future:
- **D-ark 25th** Color Support - after toy release

---

## INSTRUCTIONS TO RUN THE APP (.app and .exe)

a. For MacOS:<br/>
1. Find DigiviceColorModifier.app in the zip I shared. 
2. Open the Termianal, go to the folder where your DigiviceColorModifier.app is and then run: 
3. chmod +x DigiviceColorModifier.app/Contents/MacOS/DigiviceColorModifier
4. Now run:
5. sudo xattr -cr "DigiviceColorModifier.app"
6. Double click to run the app. You can also move it to your Applications folder if you want.
7. If you get that stupid “apple could not verify app is free of malware” error, then go to System Settings -> Privacy and Securiy in left sidebar, scroll down below the Security section. Then click Open Anyway.

b. For Windows:<br/>
Find DigiviceColorModifier.exe in the zip I shared and double click open it.
      
### BUILDING THE SOURCE CODE INTO .exe or .app
1. Create a virtual environment using:<br/>
   python -m venv venv<br/><br/>

2. Activate the virtual environment you created:<br/>
	Windows:<br/>
	venv\Scripts\activate<br/><br/>

	Mac:<br/>
	source venv/bin/activate<br/><br/>

3. Run pip install -r requirements.txt<br/>
4. Run pyinstaller to package the file<br/><br/>
	
	Windows:

	pyinstaller --name "DigiviceColorModifier" --onefile --noconsole --hidden-import wave --hidden-import cffi --hidden-import _cffi_backend --collect-all imagequant --icon "icons\digivice.ico" --add-data "kindness.gif;." --add-data "scripts;scripts" scripts\digimon_tool_gui.py

	Mac:

   pyinstaller \
      --name "DigiviceColorModifier" \
      --onefile \
      --windowed \
      --hidden-import wave \
      --hidden-import cffi \
      --hidden-import _cffi_backend \
      --collect-all imagequant \
      --icon "icons/digivice.icns" \
      --add-data "kindness.gif:." \
      --add-data "scripts:scripts" \
   scripts/digimon_tool_gui.py  


  <br/>The app will be generated and stored in the dist folder.
