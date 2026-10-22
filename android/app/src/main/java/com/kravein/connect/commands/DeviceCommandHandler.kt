package com.kravein.connect.commands

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import androidx.core.content.ContextCompat
import com.kravein.connect.device.AndroidDeviceInfoProvider
import com.kravein.connect.network.HelloInfo
import com.kravein.connect.protocol.CommandResult
import com.kravein.connect.protocol.ErrorCode
import org.json.JSONObject

/** Android glue for the phone commands. [CommandRouter] has already validated names and arguments. */
class DeviceCommandHandler(private val context: Context) : DeviceBackend {
    private val infoProvider = AndroidDeviceInfoProvider(context)

    fun helloInfo(deviceName: String) = HelloInfo(deviceName, Build.VERSION.RELEASE ?: "", appVersion())

    private fun appVersion(): String = runCatching {
        context.packageManager.getPackageInfo(context.packageName, 0).versionName
    }.getOrNull().orEmpty()

    override fun execute(action: DeviceAction, args: JSONObject): CommandResult = when (action) {
        DeviceAction.GET_DEVICE_INFO -> getDeviceInfo()
        DeviceAction.GET_BATTERY -> getBattery()
        DeviceAction.FLASHLIGHT_ON -> flashlight(true)
        DeviceAction.FLASHLIGHT_OFF -> flashlight(false)
        DeviceAction.VOLUME_GET -> volumeGet()
        DeviceAction.VOLUME_SET -> volumeSet(args.getInt("percent"))
        DeviceAction.LAUNCH_APP -> launchApp(args.getString("app"))
        DeviceAction.OPEN_URL -> openUrl(args.getString("url"))
    }

    private fun getDeviceInfo() = CommandResult(
        true,
        mapOf(
            "model" to (Build.MODEL ?: "Android"),
            "os_version" to (Build.VERSION.RELEASE ?: "Unknown"),
            "app_version" to appVersion(),
        ),
    )

    private fun getBattery(): CommandResult {
        val (percent, charging) = infoProvider.batterySnapshot()
        return CommandResult(true, mapOf("percent" to percent, "charging" to charging))
    }

    private fun flashlight(enabled: Boolean): CommandResult {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            return CommandResult.failure(ErrorCode.PERMISSION_DENIED, "Camera permission is required to control the flashlight.")
        }
        val manager = context.getSystemService(Context.CAMERA_SERVICE) as? CameraManager
            ?: return CommandResult.failure(ErrorCode.UNAVAILABLE, "Camera service unavailable.")
        val cameraId = manager.cameraIdList.firstOrNull { id ->
            runCatching {
                val chars = manager.getCameraCharacteristics(id)
                val flash = chars.get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
                val facing = chars.get(CameraCharacteristics.LENS_FACING)
                flash && (facing == CameraCharacteristics.LENS_FACING_BACK || facing == CameraCharacteristics.LENS_FACING_EXTERNAL)
            }.getOrDefault(false)
        } ?: return CommandResult.failure(ErrorCode.UNAVAILABLE, "No flashlight on this device.")
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                val maxStrength = runCatching {
                    manager.getCameraCharacteristics(cameraId).get(CameraCharacteristics.FLASH_INFO_STRENGTH_MAXIMUM_LEVEL) ?: 1
                }.getOrDefault(1)
                if (enabled && maxStrength > 1) {
                    manager.turnOnTorchWithStrengthLevel(cameraId, maxStrength)
                } else {
                    manager.setTorchMode(cameraId, enabled)
                }
            } else {
                manager.setTorchMode(cameraId, enabled)
            }
            CommandResult(true, mapOf("flashlight" to if (enabled) "on" else "off"))
        } catch (security: SecurityException) {
            CommandResult.failure(ErrorCode.PERMISSION_DENIED, security.message ?: "Flashlight permission denied.")
        } catch (exc: Exception) {
            CommandResult.failure(ErrorCode.UNAVAILABLE, exc.message ?: "Flashlight unavailable.")
        }
    }

    private fun launchApp(requested: String): CommandResult {
        val launcherIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = runCatching { context.packageManager.queryIntentActivities(launcherIntent, 0) }
            .getOrDefault(emptyList())
            .map { AppEntry(it.loadLabel(context.packageManager)?.toString().orEmpty(), it.activityInfo.packageName) }

        val packageName = when (val match = resolveApp(requested, apps)) {
            is AppMatch.Found -> match.packageName
            is AppMatch.Ambiguous -> {
                val names = match.candidates.take(5).joinToString(", ") { "${it.label} (${it.packageName})" }
                return CommandResult.failure(ErrorCode.INVALID_ARGS, "More than one app matches '$requested': $names. Use the package name.")
            }
            AppMatch.NotFound -> requested.takeIf { context.packageManager.getLaunchIntentForPackage(it) != null }
                ?: return CommandResult.failure(ErrorCode.FAILED, "No installed app matches: $requested")
        }
        val intent = context.packageManager.getLaunchIntentForPackage(packageName)
            ?: return CommandResult.failure(ErrorCode.FAILED, "Package not launchable: $packageName")
        return try {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            CommandResult(true, mapOf("package" to packageName))
        } catch (exc: Exception) {
            CommandResult.failure(ErrorCode.FAILED, exc.message ?: "Unable to launch app.")
        }
    }

    private fun openUrl(url: String): CommandResult = try {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        CommandResult(true, mapOf("url" to url))
    } catch (exc: Exception) {
        CommandResult.failure(ErrorCode.FAILED, exc.message ?: "Unable to open URL.")
    }

    private fun audio() = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager

    private fun volumeGet(): CommandResult {
        val audio = audio() ?: return CommandResult.failure(ErrorCode.UNAVAILABLE, "Audio service unavailable.")
        val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
        val current = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
        return CommandResult(true, mapOf("percent" to ((current * 100f) / max).toInt().coerceIn(0, 100)))
    }

    private fun volumeSet(percent: Int): CommandResult {
        val audio = audio() ?: return CommandResult.failure(ErrorCode.UNAVAILABLE, "Audio service unavailable.")
        val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, volumeLevel(percent, max), 0)
        return CommandResult(true, mapOf("percent" to percent))
    }
}
