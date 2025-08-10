// Basic Three.js scene setup
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 100000);
const renderer = new THREE.WebGLRenderer();

renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

// Add a light source
const ambientLight = new THREE.AmbientLight(0x333333);
scene.add(ambientLight);

const sunLight = new THREE.PointLight(0xffffff, 2, 0, 2);
scene.add(sunLight);


// Data for the celestial bodies
const G = 6.67430e-11;
const timeStep = 0.01; // Adjust this for simulation speed and stability

// Scaling factors
const realisticSizeScale = 0.00005;
const realisticDistanceScale = 0.000000001;
const logSizeScale = 0.5;
const logDistanceScale = 20;


const celestialBodiesData = [
    { name: 'Sun', mass: 1.989e30, diameter: 1392700, distance: 0, color: 0xffff00, rotationPeriod: 58.65 * 24, axialTilt: 0.034, orbitalPeriod: 0 },
    { name: 'Mercury', mass: 0.330e24, diameter: 4879, distance: 57.9e9, color: 0xaaaaaa, rotationPeriod: 1407.6, axialTilt: 0.034, orbitalPeriod: 88.0 },
    { name: 'Venus', mass: 4.87e24, diameter: 12104, distance: 108.2e9, color: 0xffd700, rotationPeriod: -5832.5, axialTilt: 177.4, orbitalPeriod: 224.7 },
    { name: 'Earth', mass: 5.97e24, diameter: 12756, distance: 149.6e9, color: 0x0000ff, rotationPeriod: 23.9, axialTilt: 23.4, orbitalPeriod: 365.2 },
    { name: 'Mars', mass: 0.642e24, diameter: 6792, distance: 228.0e9, color: 0xff4500, rotationPeriod: 24.6, axialTilt: 25.2, orbitalPeriod: 687.0 },
    { name: 'Jupiter', mass: 1898e24, diameter: 142984, distance: 778.5e9, color: 0xffa500, rotationPeriod: 9.9, axialTilt: 3.1, orbitalPeriod: 4331 },
    { name: 'Saturn', mass: 568e24, diameter: 120536, distance: 1432.0e9, color: 0xf0e68c, rotationPeriod: 10.7, axialTilt: 26.7, orbitalPeriod: 10747 },
    { name: 'Uranus', mass: 86.8e24, diameter: 51118, distance: 2867.0e9, color: 0xadd8e6, rotationPeriod: -17.2, axialTilt: 97.8, orbitalPeriod: 30589 },
    { name: 'Neptune', mass: 102e24, diameter: 49528, distance: 4515.0e9, color: 0x00008b, rotationPeriod: 16.1, axialTilt: 28.3, orbitalPeriod: 59800 }
];

const bodies = [];
let isLogarithmicScale = false;

// Create the celestial bodies
celestialBodiesData.forEach(bodyData => {
    const geometry = new THREE.SphereGeometry(1, 32, 32); // Start with a radius of 1
    const material = new THREE.MeshStandardMaterial({ color: bodyData.color });
    const bodyMesh = new THREE.Mesh(geometry, material);

    const container = new THREE.Object3D();
    container.velocity = new THREE.Vector3(0, 0, 0);
    container.acceleration = new THREE.Vector3(0, 0, 0);
    container.mass = bodyData.mass;

    // Axial Tilt
    const tiltInRadians = bodyData.axialTilt * (Math.PI / 180);
    bodyMesh.rotation.x = tiltInRadians;

    container.add(bodyMesh);

    // Orbital Path
    const pathPoints = [];
    const orbitRadius = bodyData.distance * realisticDistanceScale;
    for (let i = 0; i <= 360; i++) {
        const angle = (i * Math.PI) / 180;
        const x = orbitRadius * Math.cos(angle);
        const z = orbitRadius * Math.sin(angle);
        pathPoints.push(new THREE.Vector3(x, 0, z));
    }
    const pathGeometry = new THREE.BufferGeometry().setFromPoints(pathPoints);
    const pathMaterial = new THREE.LineBasicMaterial({ color: 0xffffff });
    const orbitalPath = new THREE.Line(pathGeometry, pathMaterial);
    orbitalPath.visible = document.getElementById('path-toggle').checked;
    scene.add(orbitalPath);

    bodies.push({ mesh: container, planetMesh: bodyMesh, orbitalPath: orbitalPath, ...bodyData });
    scene.add(container);
});

function updateScales() {
    isLogarithmicScale = document.getElementById('scale-toggle').checked;

    bodies.forEach(body => {
        const bodyData = body;
        if (isLogarithmicScale) {
            // Logarithmic Scale
            const radius = bodyData.name === 'Sun' ? Math.log10(bodyData.diameter) * logSizeScale * 2 : Math.log10(bodyData.diameter) * logSizeScale;
            body.planetMesh.scale.set(radius, radius, radius);
            if (bodyData.distance > 0) {
                body.mesh.position.x = (Math.log10(bodyData.distance) - 9) * logDistanceScale;
            } else {
                body.mesh.position.x = 0;
            }
            body.orbitalPath.visible = false; // Hide paths in log scale
        } else {
            // Realistic Scale
            const radius = (bodyData.diameter / 2) * realisticSizeScale;
            body.planetMesh.scale.set(radius, radius, radius);
            body.mesh.position.x = bodyData.distance * realisticDistanceScale;
            body.orbitalPath.visible = document.getElementById('path-toggle').checked;
        }

        // Update orbital path scale
        const scale = isLogarithmicScale ? (Math.log10(bodyData.distance) - 9) * logDistanceScale / (bodyData.distance * realisticDistanceScale) : 1;
        if (body.orbitalPath && bodyData.distance > 0) {
            body.orbitalPath.scale.set(scale, scale, scale);
        }


        // Initial velocity for orbit (needs to be set after position is known)
        if (bodyData.name !== 'Sun') {
            const distance = body.mesh.position.clone().length();
            const sunMass = celestialBodiesData[0].mass;
            const speed = Math.sqrt((G * sunMass * realisticDistanceScale) / distance); // a fudge factor to make it look ok
            body.mesh.velocity.z = -speed;
        }
    });
}

// Initial setup
updateScales();

// Event listener for the toggles
document.getElementById('scale-toggle').addEventListener('change', updateScales);
document.getElementById('path-toggle').addEventListener('change', () => {
    const showPaths = document.getElementById('path-toggle').checked;
    bodies.forEach(body => {
        if (body.orbitalPath) {
            body.orbitalPath.visible = showPaths && !isLogarithmicScale;
        }
    });
});


// Set camera position
camera.position.z = 300;

// Camera Controls
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
let selectedBody = null;

// Raycasting for planet selection
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

function updateInfoPanel(body) {
    const infoPanel = document.getElementById('info-panel');
    if (body) {
        infoPanel.style.display = 'block';
        document.getElementById('info-name').textContent = body.name;
        document.getElementById('info-mass').textContent = `${body.mass.toExponential(2)} kg`;
        document.getElementById('info-diameter').textContent = `${body.diameter.toLocaleString()} km`;
        document.getElementById('info-distance').textContent = `${(body.distance / 1e6).toLocaleString()} million km`;
        document.getElementById('info-orbital-period').textContent = body.orbitalPeriod > 0 ? `${Math.round(body.orbitalPeriod)} days` : 'N/A';
    } else {
        infoPanel.style.display = 'none';
    }
}

function onMouseDown(event) {
    mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
    mouse.y = - (event.clientY / window.innerHeight) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);

    const intersects = raycaster.intersectObjects(scene.children, true);

    if (intersects.length > 0) {
        const intersectedObject = intersects[0].object;
        // Find the body that this mesh belongs to
        const body = bodies.find(b => b.planetMesh === intersectedObject || b.mesh === intersectedObject);
        if (body) {
            selectedBody = body;
            updateInfoPanel(body);
        }
    } else {
        selectedBody = null;
        updateInfoPanel(null);
    }
}

window.addEventListener('mousedown', onMouseDown, false);


// Animation loop
function animate() {
    requestAnimationFrame(animate);

    controls.update();

    if (selectedBody) {
        controls.target.copy(selectedBody.mesh.position);
    }


    if (!isLogarithmicScale) {
        // Physics calculations (only for realistic scale)
        for (let i = 0; i < bodies.length; i++) {
            bodies[i].mesh.acceleration.set(0, 0, 0);
            for (let j = 0; j < bodies.length; j++) {
                if (i === j) continue;

                const bodyA = bodies[i];
                const bodyB = bodies[j];

                const distanceVector = new THREE.Vector3().subVectors(bodyB.mesh.position, bodyA.mesh.position);
                const distance = distanceVector.length() / realisticDistanceScale; // Use real distance for physics

                if (distance === 0) continue;

                const forceDirection = distanceVector.clone().normalize();
                const forceMagnitude = (G * bodyA.mass * bodyB.mass) / (distance * distance);
                const force = forceDirection.multiplyScalar(forceMagnitude);

                bodyA.mesh.acceleration.add(force.divideScalar(bodyA.mass));
            }
        }

        // Update velocities and positions
        for (let i = 0; i < bodies.length; i++) {
            const body = bodies[i];
            body.mesh.velocity.add(body.mesh.acceleration.multiplyScalar(timeStep));
            body.mesh.position.add(body.mesh.velocity.multiplyScalar(timeStep * realisticDistanceScale));
        }
    }


    // Planet rotation
    for (let i = 0; i < bodies.length; i++) {
        const body = bodies[i];
        const rotationSpeed = (2 * Math.PI) / (body.rotationPeriod * 3600); // convert hours to seconds
        body.planetMesh.rotation.y += rotationSpeed * timeStep * 1000; // Adjust for simulation speed
    }


    renderer.render(scene, camera);
}

animate();

// Handle window resizing
window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});
