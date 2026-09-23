package kz.samruk.meetingai.network

import kz.samruk.meetingai.model.MeetingDetail
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

interface MeetingApiService {
    @GET("/api/meetings/{id}")
    suspend fun getMeeting(@Path("id") id: String): MeetingDetail

    @POST("/api/meetings/{id}/toggle-task")
    suspend fun toggleTask(
        @Path("id") id: String,
        @Body payload: Map<String, String>
    ): Response<ResponseBody>

    @POST("/api/export-docx/{id}")
    suspend fun exportDocx(@Path("id") id: String): Response<ResponseBody>
}

object ApiClient {
    // 10.0.2.2 is localhost for Android Emulator, or replace with local Wi-Fi IP (e.g. 192.168.1.50)
    var baseUrl: String = "http://10.0.2.2:8000"

    private fun getRetrofit(): Retrofit {
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    val service: MeetingApiService by lazy {
        getRetrofit().create(MeetingApiService::class.java)
    }
}
