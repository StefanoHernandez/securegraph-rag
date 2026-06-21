"use client";

import React, { useEffect, useRef } from "react";
import * as THREE from "three";

interface ThreeBackgroundProps {
  particleCount?: number;
  opacity?: number;
}

export default function ThreeBackground({
  particleCount = 120,
  opacity = 0.6,
}: ThreeBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Respect user prefers-reduced-motion setting
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (mediaQuery.matches) return;

    // Renderer
    const renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);

    // Scene
    const scene = new THREE.Scene();

    // Camera
    const camera = new THREE.PerspectiveCamera(
      60,
      window.innerWidth / window.innerHeight,
      1,
      4000
    );
    camera.position.z = 1800;

    // Config variables
    const r = 1600;
    const rHalf = r / 2;
    const maxConnections = 6;
    const minDistance = 280;

    // Particles Data
    const particlesData: Array<{
      velocity: THREE.Vector3;
      numConnections: number;
    }> = [];
    const particlePositions = new Float32Array(particleCount * 3);
    const particleGeometry = new THREE.BufferGeometry();

    for (let i = 0; i < particleCount; i++) {
      const x = Math.random() * r - rHalf;
      const y = Math.random() * r - rHalf;
      const z = Math.random() * r - rHalf;

      particlePositions[i * 3] = x;
      particlePositions[i * 3 + 1] = y;
      particlePositions[i * 3 + 2] = z;

      particlesData.push({
        velocity: new THREE.Vector3(
          -0.3 + Math.random() * 0.6,
          -0.3 + Math.random() * 0.6,
          -0.3 + Math.random() * 0.6
        ),
        numConnections: 0,
      });
    }

    particleGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(particlePositions, 3).setUsage(
        THREE.DynamicDrawUsage
      )
    );
    particleGeometry.setDrawRange(0, particleCount);

    // Material
    const pointsMaterial = new THREE.PointsMaterial({
      color: 0x818cf8,
      size: 4,
      blending: THREE.AdditiveBlending,
      transparent: true,
      opacity: opacity,
      sizeAttenuation: true,
    });

    const pointCloud = new THREE.Points(particleGeometry, pointsMaterial);
    scene.add(pointCloud);

    // Segment lines
    const segments = particleCount * particleCount;
    const linePositions = new Float32Array(segments * 3);
    const lineColors = new Float32Array(segments * 3);

    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(linePositions, 3).setUsage(THREE.DynamicDrawUsage)
    );
    lineGeometry.setAttribute(
      "color",
      new THREE.BufferAttribute(lineColors, 3).setUsage(THREE.DynamicDrawUsage)
    );
    lineGeometry.computeBoundingSphere();
    lineGeometry.setDrawRange(0, 0);

    const lineMaterial = new THREE.LineBasicMaterial({
      vertexColors: true,
      blending: THREE.AdditiveBlending,
      transparent: true,
      opacity: opacity * 0.67,
    });

    const linesMesh = new THREE.LineSegments(lineGeometry, lineMaterial);
    scene.add(linesMesh);

    // Color Palette
    const colors3 = [
      new THREE.Color(0x818cf8), // indigo
      new THREE.Color(0x22d3ee), // cyan
      new THREE.Color(0x34d399), // emerald
    ];

    let animationFrameId: number;

    const animate = () => {
      // Pause when hidden to save CPU/GPU cycles
      if (document.hidden) {
        animationFrameId = requestAnimationFrame(animate);
        return;
      }

      let vertexpos = 0;
      let colorpos = 0;
      let numConnected = 0;

      for (let i = 0; i < particleCount; i++) {
        particlesData[i].numConnections = 0;
      }

      for (let i = 0; i < particleCount; i++) {
        const pData = particlesData[i];

        particlePositions[i * 3] += pData.velocity.x;
        particlePositions[i * 3 + 1] += pData.velocity.y;
        particlePositions[i * 3 + 2] += pData.velocity.z;

        // Bounce off bounds
        if (
          particlePositions[i * 3] < -rHalf ||
          particlePositions[i * 3] > rHalf
        )
          pData.velocity.x = -pData.velocity.x;
        if (
          particlePositions[i * 3 + 1] < -rHalf ||
          particlePositions[i * 3 + 1] > rHalf
        )
          pData.velocity.y = -pData.velocity.y;
        if (
          particlePositions[i * 3 + 2] < -rHalf ||
          particlePositions[i * 3 + 2] > rHalf
        )
          pData.velocity.z = -pData.velocity.z;

        if (pData.numConnections >= maxConnections) continue;

        for (let j = i + 1; j < particleCount; j++) {
          const pDataB = particlesData[j];
          if (pDataB.numConnections >= maxConnections) continue;

          const dx = particlePositions[i * 3] - particlePositions[j * 3];
          const dy = particlePositions[i * 3 + 1] - particlePositions[j * 3 + 1];
          const dz = particlePositions[i * 3 + 2] - particlePositions[j * 3 + 2];
          const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

          if (dist < minDistance) {
            pData.numConnections++;
            pDataB.numConnections++;

            const alpha = 1.0 - dist / minDistance;
            const c = colors3[(i + j) % 3];

            linePositions[vertexpos++] = particlePositions[i * 3];
            linePositions[vertexpos++] = particlePositions[i * 3 + 1];
            linePositions[vertexpos++] = particlePositions[i * 3 + 2];

            linePositions[vertexpos++] = particlePositions[j * 3];
            linePositions[vertexpos++] = particlePositions[j * 3 + 1];
            linePositions[vertexpos++] = particlePositions[j * 3 + 2];

            lineColors[colorpos++] = c.r * alpha;
            lineColors[colorpos++] = c.g * alpha;
            lineColors[colorpos++] = c.b * alpha;

            lineColors[colorpos++] = c.r * alpha;
            lineColors[colorpos++] = c.g * alpha;
            lineColors[colorpos++] = c.b * alpha;

            numConnected++;
          }
        }
      }

      linesMesh.geometry.setDrawRange(0, numConnected * 2);
      linesMesh.geometry.attributes.position.needsUpdate = true;
      linesMesh.geometry.attributes.color.needsUpdate = true;
      pointCloud.geometry.attributes.position.needsUpdate = true;

      const time = Date.now() * 0.00008;
      pointCloud.rotation.y = time;
      linesMesh.rotation.y = time;

      renderer.render(scene, camera);
      animationFrameId = requestAnimationFrame(animate);
    };

    animate();

    const handleResize = () => {
      renderer.setSize(window.innerWidth, window.innerHeight);
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
    };

    window.addEventListener("resize", handleResize);

    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener("resize", handleResize);
      renderer.dispose();
      pointCloud.geometry.dispose();
      pointsMaterial.dispose();
      lineGeometry.dispose();
      lineMaterial.dispose();
    };
  }, [particleCount, opacity]);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 w-full h-full pointer-events-none -z-50"
    />
  );
}
