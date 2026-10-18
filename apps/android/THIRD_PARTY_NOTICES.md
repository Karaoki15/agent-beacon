# Third-party notices

Agent Beacon Android bundles the following Apache-2.0 libraries through the pinned QR scanner dependency. Preserve their license notices when redistributing the APK.

The app declares the scanner's AndroidX runtime dependencies explicitly because JourneyApps 4.3.0's published Maven POM omits its `implementation` dependencies. Keep these versions aligned with the JourneyApps 4.3.0 build: AndroidX Core 1.6.0 and Fragment 1.3.6.

| Artifact | Version | License | Project |
| --- | --- | --- | --- |
| `com.journeyapps:zxing-android-embedded` | 4.3.0 | Apache-2.0 | [JourneyApps source and license](https://github.com/journeyapps/zxing-android-embedded/tree/v4.3.0) |
| `com.google.zxing:core` | 3.4.1 | Apache-2.0 | [ZXing source and license](https://github.com/zxing/zxing/tree/zxing-3.4.1) |
| `androidx.core:core` | 1.6.0 | Apache-2.0 | [AndroidX source and license](https://android.googlesource.com/platform/frameworks/support/+/androidx-core-release/) |
| `androidx.fragment:fragment` | 1.3.6 | Apache-2.0 | [AndroidX source and license](https://android.googlesource.com/platform/frameworks/support/+/androidx-fragment-release/) |

The complete Apache License 2.0 text is included in the source tree at `app/src/main/assets/licenses/Apache-2.0.txt` and is packaged into the APK assets. The scanner's v4.3.0 release notes document its small AndroidX dependencies and default ZXing core version: <https://github.com/journeyapps/zxing-android-embedded/releases/tag/v4.3.0>.
