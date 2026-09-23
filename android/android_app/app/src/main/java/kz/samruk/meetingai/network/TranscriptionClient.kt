package kz.samruk.meetingai.network

import android.content.Context
import android.os.Build
import kz.samruk.meetingai.session.Transcript
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.*
import java.util.concurrent.TimeUnit

data class StartMobileSession(val language: String = "")
data class MobileSessionId(val id: String)
interface TranscriptionApi {
    @POST("api/mobile/sessions")
    suspend fun create(@Body body: StartMobileSession): MobileSessionId
    @POST("api/mobile/sessions/{id}/chunks")
    suspend fun append(
        @Path("id") id: String, @Query("sequence") sequence: Int,
        @Query("offset") offset: Double, @Body pcm: RequestBody
    ): Transcript
    @POST("api/mobile/sessions/{id}/stop")
    suspend fun stop(@Path("id") id: String): Transcript
}
class MobileSettings(context: Context) {
    private val prefs = context.getSharedPreferences("mobile_transcript", Context.MODE_PRIVATE)
    var serverUrl: String
        get() = prefs.getString("server", if (Build.FINGERPRINT.contains("generic") ||
            Build.MODEL.contains("Emulator") || Build.HARDWARE.contains("ranchu"))
            "http://10.0.2.2:8770/" else "") ?: ""
        set(value) { prefs.edit().putString("server", value.trim().trimEnd('/') + "/").apply() }
    var configured: Boolean
        get() = prefs.getBoolean("configured", false)
        set(value) { prefs.edit().putBoolean("configured", value).apply() }
    var language: String
        get() = prefs.getString("language", "") ?: ""
        set(value) { prefs.edit().putString("language", value).apply() }
    companion object {
        fun validUrl(value: String): Boolean {
            val parsed = value.trim().toHttpUrlOrNull() ?: return false
            return parsed.username.isEmpty() && parsed.password.isEmpty() &&
                parsed.query == null && parsed.fragment == null && parsed.encodedPath == "/"
        }
    }
}
object TranscriptionClient {
    fun create(url: String): TranscriptionApi {
        require(MobileSettings.validUrl(url)) { "Укажите адрес сервера в настройках" }
        val client = OkHttpClient.Builder().connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS).writeTimeout(15, TimeUnit.SECONDS).build()
        return Retrofit.Builder().baseUrl(url.trimEnd('/') + "/").client(client)
            .addConverterFactory(GsonConverterFactory.create()).build()
            .create(TranscriptionApi::class.java)
    }
    fun pcmBody(bytes: ByteArray) = bytes.toRequestBody("application/octet-stream".toMediaType())
}
