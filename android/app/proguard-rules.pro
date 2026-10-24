# R8 rules for the release build. Libraries ship their own consumer rules (OkHttp, CameraX, ML Kit, Compose);
# only what they miss is listed here.

# security-crypto pulls Tink, which references annotation-only classes that are not on the classpath.
-dontwarn com.google.errorprone.annotations.**
-dontwarn javax.annotation.**
-dontwarn com.google.crypto.tink.**

# OkHttp optional TLS providers.
-dontwarn org.bouncycastle.**
-dontwarn org.conscrypt.**
-dontwarn org.openjsse.**
