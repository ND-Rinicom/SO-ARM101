import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

// --- BASIC SETUP ---
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);

const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

// --- GLOBALS ---
let model;
let wireframeMode = false;
let jointAxisConfig = null; // Joint axis configuration loaded from JSON

const bonesByName = new Map();
const objectsByName = new Map(); // For models without bones
const initialRotations = new Map(); // Store initial/rest rotations for each bone/object

let modelPositionOffset = { x: 0, y: 0, z: 0 }; // Configurable model position offset
let modelRotationOffset = { x: 0, y: Math.PI, z: 0 }; // Configurable model rotation offset (default 180° on X)

const outlineMap = new WeakMap(); // mesh -> lineSegments

const OUTLINE_COLOR = 0x000000;
const EDGE_THRESHOLD_ANGLE = 15; // degrees; increase to show fewer edges

const outlineMaterial = new THREE.LineBasicMaterial({
  color: OUTLINE_COLOR,
  transparent: true,
  opacity: 1,
});

const wireframeOutlineMaterial = new THREE.LineBasicMaterial({
  color: 0x00ff00,
  transparent: true,
  opacity: 1,
});

// --- LOADING MODELS ---

// Load joint axis configuration from JSON file
async function loadJointConfig(configPath) {
  try {
    const response = await fetch(configPath);
    if (!response.ok) {
      console.warn(`Joint config not found: ${configPath} (status: ${response.status})`);
      return false;
    }
    jointAxisConfig = await response.json();
    console.log("Loaded joint axis configuration:", jointAxisConfig);
    return true;
  } catch (error) {
    console.error("Error loading joint config:", error);
    return false;
  }
}

// Add the outlines for our ghost mesh
function addOutlineForMesh(mesh, material = outlineMaterial, edgeThresholdAngle = EDGE_THRESHOLD_ANGLE) {
  // Extract edges from the mesh where adjacent faces meet at an angle > EDGE_THRESHOLD_ANGLE
  // This creates a cartoon-style outline by only drawing significant edges (not every triangle edge)
  const edgesGeom = new THREE.EdgesGeometry(mesh.geometry, edgeThresholdAngle);
  const lines = new THREE.LineSegments(edgesGeom, material);

  // Make it follow the mesh (including skinning transforms) by parenting it to the mesh
  mesh.add(lines);

  // Push it slightly outward to reduce z-fighting.
  lines.scale.setScalar(1.001);

  // draw lines after the ghost surface
  lines.renderOrder = 10;

  outlineMap.set(mesh, lines);
}

function getMaterial() {
  if (!wireframeMode) {
    return new THREE.MeshStandardMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.8,

      // Stop objects behind the model showing through
      blending: THREE.NoBlending,
    });
  } else {
    return new THREE.MeshBasicMaterial({
      colorWrite: false,
    });
  }
}

function loadModel(modelBasePath) {
  const loader = new GLTFLoader();
  console.log("Loading model:", modelBasePath);

  // Add extensions for model and config files
  const modelPath = modelBasePath + '.glb';
  const configPath = modelBasePath + '.json';

  return new Promise((resolve, _reject) => {
    // Load joint config first
    loadJointConfig(configPath).then(() => {
      // Check if model exists first
      fetch(modelPath, { method: 'HEAD' })
        .then(response => {
          if (!response.ok) {
            console.warn(`Model not found: ${modelPath} (status: ${response.status})`);
            resolve(false);
            return;
          }

          // Model exists, proceed with loading
          loader.load(
            modelPath,
            (gltf) => {
            model = gltf.scene;
            
            // Apply custom position offset
            model.position.add(new THREE.Vector3(modelPositionOffset.x, modelPositionOffset.y, modelPositionOffset.z));
            
            // Apply custom rotation offset
            model.rotation.set(modelRotationOffset.x, modelRotationOffset.y, modelRotationOffset.z);

            // Get material (wireframe or ghost)
            const material = getMaterial();
            material.side = THREE.DoubleSide; // Ensure both sides are rendered

            // Apply materials + collect bones
            model.traverse((child) => {
              console.log("Model child:", child.name, child.type);
              if (child.isMesh) {
                child.material = material;

                // Add outlines (green for wireframe mode, black for ghost mode)
                if (wireframeMode) {
                  addOutlineForMesh(child, wireframeOutlineMaterial);
                } else {
                  addOutlineForMesh(child);
                }
              }

              if (child.isBone) {
                const lowerName = child.name.toLowerCase();
                bonesByName.set(lowerName, child);
                // Store the initial rotation as the "rest position"
                initialRotations.set(lowerName, {
                  x: child.rotation.x,
                  y: child.rotation.y,
                  z: child.rotation.z
                });
              } else if (child.name) {
                // Store all named objects as potential joints (for non-skeletal models)
                const lowerName = child.name.toLowerCase();
                objectsByName.set(lowerName, child);
                // Store the initial rotation as the "rest position"
                initialRotations.set(lowerName, {
                  x: child.rotation.x,
                  y: child.rotation.y,
                  z: child.rotation.z
                });
              }
            });

            scene.add(model);
            
            resolve(true);
            console.log("Loaded model:", model);
          },
          undefined,
          (error) => {
            console.error(`Error loading model: ${modelPath}`, error);
            resolve(false);
          }
        );
      })
      .catch(error => {
        console.warn(`Failed to check model existence: ${modelPath}`, error);
        resolve(false);
      });
    });
  });
}

// --- CONFIGURE LOADED MODELS ---

// Set all given joint angles using the joint axis configuration
function setJointAngles(jointAngles) {
  // If no joint config loaded, fall back to old behavior
  if (!jointAxisConfig || !jointAxisConfig.joints) {
    console.warn("No joint configuration loaded, using fallback method");
    for (const jointName in jointAngles) {
      const axes = jointAngles[jointName];
      for (const axis in axes) {
        setRotation(jointName, axis, axes[axis]);
        console.log(`Set joint ${jointName} axis ${axis} to ${axes[axis]} degrees`);
      }
    }
  } else {
    // Use joint config to automatically determine axis
    for (const jointName in jointAngles) {
      // Strip .pos suffix if present
      const cleanName = jointName.endsWith(".pos") ? jointName.slice(0, -4) : jointName;
      
      // Get the axis for this joint from config
      const axis = jointAxisConfig.joints[cleanName];
      if (!axis) {
        console.warn(`No axis configuration found for joint: ${cleanName}`);
        continue;
      }
      
      // Get the angle value
      let angle = jointAngles[jointName];
      
      // Special handling for gripper: convert from 0-100 normalized to 0 to -127 degrees
      if (cleanName === "gripper") {
        angle = -(angle / 100) * 127;  // 0 -> 0°, 100 -> -127°
      }
      
      // Set the rotation
      setRotation(cleanName, axis, angle);
      console.log(`Set joint ${cleanName} axis ${axis} to ${angle} degrees`);
    }
  }
  
  // Render the scene after updating joint angles
  renderer.render(scene, camera);
}

// Set rotation of a named bone
function setRotation(jointName, axis, valueDeg) {
  // Strip ".pos" suffix if present
  if (jointName.endsWith(".pos")) {
    jointName = jointName.slice(0, -4);
  }
  
  // Try to find bone first, then fall back to regular object
  const bone = bonesByName.get(jointName) || objectsByName.get(jointName);
  if (!bone) {
    console.warn("No bone or object found for jointName:", jointName, "Known bones:", [...bonesByName.keys()], "Known objects:", [...objectsByName.keys()]);
    return;
  }

  if (axis !== "x" && axis !== "y" && axis !== "z") return;

  // Get the initial/rest rotation for this bone
  const initialRot = initialRotations.get(jointName);
  if (!initialRot) {
    console.warn("No initial rotation found for:", jointName);
    return;
  }

  // Negate Y-axis rotations
  const value = axis === "y" ? -valueDeg : valueDeg;
  
  // Apply rotation relative to the initial/rest position
  bone.rotation[axis] = initialRot[axis] + THREE.MathUtils.degToRad(value);
  bone.updateMatrixWorld(true);
}

// Set render mode to wireframe
function setRenderMode(wireframe = false) {
  if(wireframe)
  {
    console.log("Setting render mode to wireframe");
    wireframeMode = true;
  }
}

// Set model position offset (call before loadModel)
function setModelPosition(x = 0, y = 0, z = 0) {
  modelPositionOffset = { x, y, z };
  
  // If model already loaded, update its position
  if (model) {
    const box = new THREE.Box3().setFromObject(model);
    const center = box.getCenter(new THREE.Vector3());
    model.position.set(-center.x + x, -center.y + y, -center.z + z);
  }
}

// Set model rotation offset in radians (call before loadModel)
function setModelRotation(xRad = 0, yRad = 0, zRad = 0) {
  modelRotationOffset = { x: xRad, y: yRad, z: zRad };
  
  // If model already loaded, update its rotation
  if (model) {
    model.rotation.set(xRad, yRad, zRad);
  }
}

// --- CAMERA AND LIGHTING ---

// Lighting setup
const light = new THREE.DirectionalLight(0xffffff, 3);
light.position.set(-1, 2, 4);
scene.add(light);

// Configure lighting (color in hex, intensity, and position)
function setLighting(color = 0xffffff, intensity = 3, x = -1, y = 2, z = 4) {
  light.color.setHex(color);
  light.intensity = intensity;
  light.position.set(x, y, z);
}

// Set camera Z position
function setCameraPosition(zPos = 1) {
  camera.position.z = zPos;
}

// Export what your HTML needs
export { loadModel, loadJointConfig, setJointAngles, setRenderMode, setCameraPosition, setModelPosition, setModelRotation, setLighting };
