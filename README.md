# Digivice Color Modifier

## LINKS

### Discord Servers (Highly Recommended to Join)
- [The CIM/CBM Community](https://discord.com/invite/x9FeKPsMdr)
- [Digitama Hatchery](https://discord.com/invite/digimon)

### Full Download Link (with tutorials, required software, etc)
- [Download Folder](https://drive.google.com/drive/folders/1HYOpG9URBFviwJ7M_MmHEK0l_AJfyFz5?usp=sharing)

### Source Code Repo
- [GitHub Repo](https://github.com/ChosenOneWyrd/DigiviceColorModifier)

### Which digimon can be added? Any for which spritesheets exist! 
- [Vital Bracelet Digimon List](https://humulos.com/digimon/vbbe/)
- [Vital Bracelet Dimensional Digimon List](https://humulos.com/digimon/vbdm/)
- [Ranked Vital Bracelet Digimon](https://humulos.com/digimon/vbbe/ranked/)
- [Ranked Dimensional Digimon](https://humulos.com/digimon/vbdm/ranked/)

### Where to Get Sprites of Other Digimon
- [Sirec DIM Archive (Reddit)](https://www.reddit.com/r/DigimonVitalBracelet/comments/1c2xm2y/sirec_dim_archive/)
- [Google Drive Folder 1](https://drive.google.com/drive/folders/1Nh4v0p_xISOuqV755uPW4MSNBywu7h2E)
- [Google Drive Folder 2](https://drive.google.com/drive/folders/13OfTj8YD8vEZjAgZm8zlPpyYf2UESWKO?usp=sharing)

---

## FEATURES

### Currently Supported:
- Modding **D-3 25th** Color Sprites & Images
- Modding **D-3 25th** Color Digimon Names
- Modding **D-3 25th** Color Digimon Power
- Modding **D-3 25th** Color Digimon Stage
- Modding **Digivice 25th** Color Sprites & Images
- Modding **Digivice 25th** Color Digimon Names
- Modding **Digivice 25th** Color Digimon Power
- Support for both **Mac** and **Windows**

### Unsupported:
- Modding **Digivice 25th** Color Digimon Stage
- Modding **D-3 25th** Color Sounds
- Modding **Digivice 25th** Color Sounds
- Modding **D-3 25th** Color Evolution Lines
- Modding **Digivice 25th** Color Evolution Lines

### Planned for Future:
- **D-ark 25th** Color Support (Images, Names, Power) - after toy release

---

## INSTRUCTIONS TO RUN THE APP (.app and .exe)

1. **Always start by creating a BACKUP COPY of your .bin file** so that even if it gets corrupted you can start over. If you lose your .bin file, DM me on discord for a copy of mine. Again, DM me, do not message in any discord server.

2. **While changing Digimon Images**, your New Digimon Images should have the same color count as your Old Digimon Images. Use some tool like [Canvas Pixel Color Counter](https://townsean.github.io/canvas-pixel-color-counter) to find the color count of your images.

3. **While changing Digimon Names**, your New Digimon Name should have the same length as your Old Digimon Name. Use _ for whitespace. Only alphabets A-Z allowed, no numbers or symbols. Examples:
   - FLORAMON has 8 letters, but I want to change it to KUDAMON which has only 7 letters. Then in the tool, I will type `KUDAMON_` to make it 8 characters. The underscore _ represents a blank space.
   - BELIALVAMDEMON has 14 letters. And I want to change it to BEELZEBUMON X which has blank spaces and 12 letters. Then in the tool, I will type `BEELZEBUMON_X_` to make it 14 characters. The underscore _ represents a blank space.

4. **While changing Digimon Power**, the Power value needs to be between 0 to 225. Anything outside this range WILL BREAK YOUR Digivice.

5. **While changing Digimon Stage**, the Power value needs to be between 1 to 5. Anything outside this range WILL BREAK YOUR Digivice. The mapping is:
   - 1 => Child / Rookie
   - 2 => Adult / Champion
   - 3 => Perfect / Ultimate
   - 4 => Ultimate / Mega
   - 5 => Super Ultimate / Ultra

6. **DO NOT ABORT any operation once it starts**, even if you want to cancel it. Even if you want to cancel, just wait for it to complete and try again. The Cancel option of the app works most of the times, but sometimes could cause glitches. But avoid it just to be safe.

7. The app might have bugs and would be slower because the code is the first un-optimized draft. I will try to optimize it further later.

8. Running the app:<br/>
a. For MacOS:<br/>
i. Find DigiviceColorModifier.app in the zip I shared. <br/>
ii. Open the Termianal, go to the folder where your DigiviceColorModifier.app is and then run: <br/>
chmod +x DigiviceColorModifier.app/Contents/MacOS/DigiviceColorModifier<br/>
iii. Now run:<br/>
sudo xattr -cr "DigiviceColorModifier.app"<br/>
iv. Double click to run the app. You can also move it to your Applications folder if you want.<br/>
v. If you get that stupid “apple could not verify app is free of malware” error, then go to System Settings -> Privacy and Securiy in left sidebar, scroll down below the Security section. Then click Open Anyway.<br/><br/>

   b. For Windows:<br/>
Find DigiviceColorModifier.exe in the zip I shared and double click open it.

9. Install GIMP if you already haven’t. It will be needed to modify images, you can also use Photoshop if you are more comfortable using that.
10. In the app, Select your type of .bin file and the .bin file.
11. Once the .bin file gets loaded, check the RANGE HINT section to get an idea of which sprites are present where.
12. In this tutorial, lets’ change Angewomon sprites to Jewelbeemon.
13. First, extract sprites in the currently selected range.Click the Export Sprites (Current Range) button, it will create an exported_sprites folder on your Desktop. Check your Desktop folder.
14. Copy the sprites you want to change in a separate folder. I will copy them to input_sprites.
15. Now, let’s get the Jewelbeemon sprites.
16. The sprites are arranged in order: idle1, idle2, attack and dodge. Let’s edit and replace using GIMP.
17. Maximize your images to 800% zoom and use the rectangle tool to cut and paste.
18. Let’s also export the Angewomon attack callouts and replace them with Jewelbeemon’s.
19. Editing done! Let’s import now. Select your input folder in the app. input_sprites in my case.
20. First click on Update Palette.
21. Then click on Replace Sprites.
22. Now, let’s check the small partner sprites first. It worked!
23. Now, let’s check the big partner sprites.
24. Why did the colors change? Why did the app have images with wrong colors? READ INSTRUCTIONS CAREFULLY!
25. This happened because the target image has 10524 colors, but source image only allows 62 colors. So it’s time to reduce color count!
26. To reduce color count, either do it using GIMP like I am doing now, or use an online tool like https://onlinepngtools.com/decrease-png-color-count 
27. Color count is reduced and my target images have less colors than source. Now, let’s try replacing again!
28. Congratulations, you have successfully learnt to sprite mod.
29. Next, let’s look at changing the names, stage and power. The Import and Export operations for this ARE VERY SLOW AND WILL TAKE TIME TO COMPLETE RUNNING.
30. Again, before you change anything, READ THE RULES! 
31.Let’s change Angewomon name, stage and power to Jewelbeemon. Be careful of the name, stage, power instructions you read previously.



