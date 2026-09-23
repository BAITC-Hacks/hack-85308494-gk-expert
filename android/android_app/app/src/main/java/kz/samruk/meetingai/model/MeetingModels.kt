package kz.samruk.meetingai.model

import com.google.gson.annotations.SerializedName

data class MeetingDetail(
    val id: String,
    val title: String,
    val organization: String,
    val date: String,
    val time: String,
    val chairperson: String,
    val tasks: List<TaskItem>,
    val summary: MeetingSummary?
)

data class TaskItem(
    val id: String,
    val assignee: String,
    val role: String?,
    val department: String?,
    val task: String,
    val deadline: String,
    val priority: String,
    var status: String,
    val controller: String?
)

data class MeetingSummary(
    val agenda: List<String>?,
    @SerializedName("key_decisions")
    val keyDecisions: List<String>?,
    @SerializedName("direction_reports")
    val directionReports: List<DirectionReport>?
)

data class DirectionReport(
    val direction: String,
    val reporter: String,
    val indicator: String,
    val problem: String,
    val solution: String
)
