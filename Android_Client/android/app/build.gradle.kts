import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Release signing: android/key.properties (local) or CHELSY_* environment variables (CI secrets).
// The keystore and passwords are NEVER committed (see .gitignore).
val keystoreProperties = Properties().apply {
    val file = rootProject.file("key.properties")
    if (file.exists()) FileInputStream(file).use { load(it) }
}
fun signingValue(key: String, env: String): String? =
    (keystoreProperties.getProperty(key) ?: System.getenv(env))?.takeIf { it.isNotBlank() }

val releaseStoreFile = signingValue("storeFile", "CHELSY_KEYSTORE_PATH")
val hasReleaseSigning = releaseStoreFile != null && file(releaseStoreFile).exists()

android {
    namespace = "ai.chelsy.chelsy_ai_client"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "ai.chelsy.chelsy_ai_client"  // unchanged since 0.9.2
        // flutter_secure_storage (Android Keystore) requires API 23+.
        minSdk = maxOf(flutter.minSdkVersion, 23)
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(releaseStoreFile!!)
                storePassword = signingValue("storePassword", "CHELSY_KEYSTORE_PASSWORD")
                keyAlias = signingValue("keyAlias", "CHELSY_KEY_ALIAS")
                keyPassword = signingValue("keyPassword", "CHELSY_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            } else if (project.findProperty("chelsy.allowDebugSigning") == "true") {
                // Local smoke tests only. Never distribute debug-signed builds.
                signingConfig = signingConfigs.getByName("debug")
            } else {
                throw GradleException(
                    "Release signing is not configured. Create android/key.properties or set " +
                    "CHELSY_KEYSTORE_PATH / CHELSY_KEYSTORE_PASSWORD / CHELSY_KEY_ALIAS / CHELSY_KEY_PASSWORD."
                )
            }
            isMinifyEnabled = true
            isShrinkResources = true
            isDebuggable = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
