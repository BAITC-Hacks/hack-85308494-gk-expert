# Add project specific ProGuard rules here.
# By default, the flags in this file are appended to flags specified
# in the Android SDK tools proguard/proguard-android-optimize.txt.

# Retrofit
-keepattributes Signature
-keepattributes *Annotation*
-keep class retrofit2.** { *; }
-keepclasseswithmembers class * {
    @retrofit2.http.* <methods>;
}

# Gson
-keep class kz.samruk.meetingai.model.** { *; }
-keepclassmembers class kz.samruk.meetingai.model.** { *; }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**
