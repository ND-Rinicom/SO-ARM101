## Issues and TODOs

As this project was completed over a short industrial placement there are a few things that could be improved or changed.

### Model Misalignment 

The first one is some temp fixes within the 3Dmodels.js. Due to my lack of knowledge of blender and how Lerobot SO-101 has done calibration the so-101.glb has to start at the middel of all its joint movements, which wrist roll is not. This needs to be looked into in more detail with idealy a complete overhall of the callibration code. However as a Temporary Fix which works now, there is the following code: 

```bash
// Special handling for gripper: convert from 0-100 normalized to 0 to -127 degrees
if (cleanName === "gripper") {
angle = -((angle / 100) * 127);
}
else if (cleanName === "wrist_roll") {
angle = -angle-90;  // -90 TEMP FIX to align wrist_roll to correct rest position.
                    // This should be fixed properly by correcting the .glb at some point
}
```

This is fine and will work to sync and fix the model, but its not ideal and will cause issues if you want to swap out the model for another glb at a later date.

### Confusing video stream pipeline. 

The video camera pipeline might want a slight rethink and redesign.

Currently we basicly have a "dumb camera sender" running on the follower Pi which basicly just blasts RTP/H.264 to our UDP_MQTT_Bridge as cheaply as possible. Then the bridge absorbs radio jitter (rtpjitterbuffer), and exposes a stable RTSP endpoint that multiple clients can connect to withough touching the Pi/radio side. 

But this might be a complety unecessary step which could be avoided by the Pi simply hosting its own RTSP, simplifying the system architecture but this depends on system needs.