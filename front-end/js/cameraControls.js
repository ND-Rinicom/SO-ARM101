import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { camera, renderer, setCameraPose, setCameraTarget, renderScene } from "/js/3Dmodels.js";

const DEFAULTS = {
  minRadius: 1,
  maxRadius: 20,
  scaleAtRadius: 3,
  minScale: 0.02,
  maxScale: 3,
  zoomSpeed: 0.025,
  rotateSpeed: 0.6,
  panSpeed: 0.6
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function getSphericalFromOffset(offset) {
  const radius = Math.max(0.0001, offset.length());
  const phi = Math.acos(clamp(offset.y / radius, -1, 1));
  const theta = Math.atan2(offset.z, offset.x);
  return { radius, theta, phi };
}

function getZoomScale(radius, config) {
  return clamp(radius / config.scaleAtRadius, config.minScale, config.maxScale);
}

export function initCameraControls({
  jsonRpcService,
  modelName,
  cameraZPos = 1,
  zoomConfig = {},
  initialTarget = { x: 0, y: 0, z: 0 }
} = {}) {
  const config = { ...DEFAULTS, ...zoomConfig };
  const target = new THREE.Vector3(initialTarget.x, initialTarget.y, initialTarget.z);

  setCameraTarget(target.x, target.y, target.z, false);
  setCameraPose(0, 0, cameraZPos, target.x, target.y, target.z);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(target);
  controls.enableDamping = false;
  controls.enableRotate = true;
  controls.enablePan = true;
  controls.enableZoom = false;
  controls.rotateSpeed = config.rotateSpeed;
  controls.zoomSpeed = config.zoomSpeed;
  controls.panSpeed = config.panSpeed;
  controls.mouseButtons = {
    LEFT: THREE.MOUSE.ROTATE,
    MIDDLE: THREE.MOUSE.DOLLY,
    RIGHT: THREE.MOUSE.PAN
  };
  controls.update();

  let lastCameraSend = 0;
  function sendCameraUpdate() {
    if (!jsonRpcService || !jsonRpcService.clientId || !modelName) return;
    const now = Date.now();
    if (now - lastCameraSend < 50) return;
    lastCameraSend = now;

    const offset = camera.position.clone().sub(controls.target);
    const spherical = getSphericalFromOffset(offset);

    jsonRpcService.sendMessage("watchman_robotarm/" + modelName, {
      jsonrpc: "2.0",
      method: "change-camera",
      params: {
        clientId: jsonRpcService.clientId,
        radius: spherical.radius,
        theta: spherical.theta,
        phi: spherical.phi,
        camX: camera.position.x,
        camY: camera.position.y,
        camZ: camera.position.z,
        targetX: controls.target.x,
        targetY: controls.target.y,
        targetZ: controls.target.z
      }
    });
  }

  function renderFrame() {
    renderScene();
  }

  function zoomBy(delta) {
    const offset = camera.position.clone().sub(controls.target);
    const radius = offset.length();
    const zoomScale = getZoomScale(radius, config);
    const nextRadius = clamp(
      radius + delta * config.zoomSpeed * zoomScale,
      config.minRadius,
      config.maxRadius
    );
    const direction = offset.normalize();
    const nextPosition = direction.multiplyScalar(nextRadius).add(controls.target);
    setCameraPose(
      nextPosition.x,
      nextPosition.y,
      nextPosition.z,
      controls.target.x,
      controls.target.y,
      controls.target.z
    );
    controls.update();
    renderFrame();
    sendCameraUpdate();
  }

  function onControlsChange() {
    renderFrame();
    sendCameraUpdate();
  }

  controls.addEventListener("change", onControlsChange);

  renderer.domElement.addEventListener("mousedown", (event) => {
    if (event.button === 0) {
      renderer.domElement.classList.add("rotating");
      renderer.domElement.classList.remove("panning", "zooming");
    } else if (event.button === 2) {
      renderer.domElement.classList.add("panning");
      renderer.domElement.classList.remove("rotating", "zooming");
    }
  });

  renderer.domElement.addEventListener("mouseup", () => {
    renderer.domElement.classList.remove("rotating", "panning", "zooming");
  });

  renderer.domElement.addEventListener("wheel", (event) => {
    event.preventDefault();
    renderer.domElement.classList.add("zooming");
    zoomBy(event.deltaY);
    clearTimeout(zoomCursorTimeout);
    zoomCursorTimeout = setTimeout(() => {
      renderer.domElement.classList.remove("zooming");
    }, 200);
  }, { passive: false });

  let zoomCursorTimeout = null;

  renderer.domElement.addEventListener("contextmenu", (event) => {
    event.preventDefault();
  });

  renderFrame();

  function applyRemoteCamera(params = {}) {
    const radius = typeof params.radius === "number"
      ? clamp(params.radius, config.minRadius, config.maxRadius)
      : camera.position.length();
    const theta = typeof params.theta === "number" ? params.theta : 0;
    const phi = typeof params.phi === "number" ? params.phi : Math.PI / 2;

    const hasCameraPosition =
      typeof params.camX === "number" &&
      typeof params.camY === "number" &&
      typeof params.camZ === "number";

    if (typeof params.targetX === "number") target.x = params.targetX;
    if (typeof params.targetY === "number") target.y = params.targetY;
    if (typeof params.targetZ === "number") target.z = params.targetZ;

    const x = hasCameraPosition
      ? params.camX
      : radius * Math.sin(phi) * Math.cos(theta) + target.x;
    const y = hasCameraPosition
      ? params.camY
      : radius * Math.cos(phi) + target.y;
    const z = hasCameraPosition
      ? params.camZ
      : radius * Math.sin(phi) * Math.sin(theta) + target.z;

    controls.target.copy(target);
    setCameraPose(x, y, z, target.x, target.y, target.z);
    controls.update();
    renderFrame();
  }

  return {
    applyRemoteCamera,
    render: renderFrame
  };
}
