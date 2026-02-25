# Notes
- Could potentianly implement a "hybrid" arm model that takes best of both from follower and leader.

# DONE
- Improve README setup instructions
- Finish implementing 3d manipulation of frontend camera (maybe add moving of arm controls aswell)
- Implement feedback values from the follower and display this as a secondary ghost within the front end 

# Problems
- Currently if follower arm dies we will keep and brocast the last known position but this probably differs due to weights of the arm

# TO DO
- Camera streaming
- Add easier to read error logging.
- Fix physical wrist roll on follower
- Amend blender model as wrist pitch is at an angle
- Add talked about #? method decleration in front end.
- Improve wireframe of the model on front end
- Finish README & add improved comments
- Report writing: Finish new diagrams & describe project structure, justifying decisions.
- Maybe adjust clamping to be less strict if safe to do so
- windows support

# Next steps for project 

Option 1:
- Get Pi receiving and sending values via radio, this will possibly help towards the mining project that Rincom are working on.

Option 2: 
- Start working on "bomb defusal drone". Working on a way to attach the arm to a drone safely and making a digital twin of it.

Option 3: 
- Improve my computer vision human arm -> so-101 arm implementation and get this safely sending to the real physical follower so-101 arm

Option 4: 
- The so-101 arm has extensive documentation and resources on ML applications. I could train the robot arm to perform a task like putting a cube in a box and then have the digital twin showing what the arm is doing.