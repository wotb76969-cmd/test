# Flutter + plugins keep rules are provided by their AARs; strip logging in release.
-assumenosideeffects class android.util.Log {
    public static *** d(...);
    public static *** v(...);
    public static *** i(...);
}
