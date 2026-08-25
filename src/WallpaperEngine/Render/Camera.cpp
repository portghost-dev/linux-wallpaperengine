#include <algorithm>
#include <cmath>
#include <glm/glm.hpp>
#include <glm/gtc/matrix_transform.hpp>

#include "Camera.h"
#include "WallpaperEngine/Logging/Log.h"

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render;

Camera::Camera (Wallpapers::CScene& scene, const SceneData::Camera& camera) :
    m_width (0), m_height (0), m_camera (camera), m_scene (scene) {
    // get the lookat position
    // TODO: ENSURE THIS IS ONLY USED WHEN NOT DOING AN ORTOGRAPHIC CAMERA AS IT THROWS OFF POINTS
    this->m_lookat = glm::lookAt (this->getEye (), this->getCenter (), this->getUp ());
}

Camera::~Camera () = default;

const glm::vec3& Camera::getCenter () const {
    return this->m_hasScriptedView ? this->m_scriptedCenter : this->m_camera.configuration.center;
}

const glm::vec3& Camera::getEye () const {
    return this->m_hasScriptedView ? this->m_scriptedEye : this->m_camera.configuration.eye;
}

const glm::vec3& Camera::getUp () const { return this->m_camera.configuration.up; }

const glm::mat4& Camera::getProjection () const { return this->m_projection; }

const glm::mat4& Camera::getScreenProjection () const { return this->m_screenProjection; }

const glm::mat4& Camera::getLookAt () const { return this->m_lookat; }

bool Camera::isOrthogonal () const { return this->m_isOrthogonal; }

Wallpapers::CScene& Camera::getScene () const { return this->m_scene; }

float Camera::getWidth () const { return this->m_width; }

float Camera::getHeight () const { return this->m_height; }

float Camera::getFov () const { return this->m_camera.projection.fov->value->getFloat (); }

float Camera::getOverrideFov () const { return this->m_camera.projection.overrideFov->value->getFloat (); }

float Camera::getNearZ () const { return this->m_camera.projection.nearz->value->getFloat (); }

float Camera::getFarZ () const { return this->m_camera.projection.farz->value->getFloat (); }

void Camera::setOrthogonalProjection (const float width, const float height) {
    float zoom = this->m_camera.projection.zoom->value->getFloat ();
    if (zoom <= 0.0f) {
	zoom = 1.0f;
    }

    this->m_width = width / zoom;
    this->m_height = height / zoom;

    const float halfRange = std::max (this->m_camera.projection.farz->value->getFloat (), 1000.0f);

    this->m_projection = glm::ortho<float> (
	-this->m_width / 2.0, this->m_width / 2.0, -this->m_height / 2.0, this->m_height / 2.0, -halfRange, halfRange
    );
    // 2D content uses the same matrix here (ortho scene = screen space already)
    this->m_screenProjection = this->m_projection;
    this->m_lookat = glm::mat4 (1.0f);
    this->m_isOrthogonal = true;
}
void Camera::setPerspectiveProjection (const float width, const float height) {
    this->m_width = width;
    this->m_height = height;

    const float nearZ = std::max (this->getNearZ (), 0.01f);
    const float farZ = std::max (this->getFarZ (), nearZ + 1.0f);

    this->m_projection = glm::scale (glm::mat4 (1.0f), glm::vec3 (1.0f, -1.0f, 1.0f))
	* glm::perspective (glm::radians (this->getFov ()), width / height, nearZ, farZ);
    const float halfRange = std::max (farZ, 1000.0f);
    this->m_screenProjection
	= glm::ortho<float> (-width / 2.0f, width / 2.0f, -height / 2.0f, height / 2.0f, -halfRange, halfRange);
    // unlike the ortho path (editor-viewport state, runtime-inert), a perspective
    // scene's authored eye/center/up IS the runtime view
    this->m_lookat = glm::lookAt (this->getEye (), this->getCenter (), this->getUp ());
    this->m_isOrthogonal = false;
}

void Camera::setScriptedView (const glm::vec3& eye, const glm::vec3& center) {
    static const bool s_camProbe = getenv ("LWE_CAMPROBE") != nullptr;
    static int s_camProbeCount = 0;
    if (s_camProbe && s_camProbeCount < 40 && ++s_camProbeCount > 0) {
	sLog.out (
	    "LWE-CAMPROBE eye=(", eye.x, ",", eye.y, ",", eye.z, ") center=(", center.x, ",", center.y, ",",
	    center.z, ")"
	);
    }
    const glm::vec3 d = center - eye;
    if (!std::isfinite (eye.x + eye.y + eye.z + center.x + center.y + center.z)
	|| glm::dot (d, d) < 1e-12f) {
	return;
    }
    this->m_hasScriptedView = true;
    this->m_scriptedEye = eye;
    this->m_scriptedCenter = center;

    if (!this->m_isOrthogonal) {
	this->m_lookat = glm::lookAt (this->getEye (), this->getCenter (), this->getUp ());
    }
}
