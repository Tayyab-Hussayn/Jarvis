package com.kravein.connect.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.ui.text.withStyle
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kravein.connect.chat.Block
import com.kravein.connect.chat.ChatState
import com.kravein.connect.chat.ChatTimeline
import com.kravein.connect.chat.ConfirmItem
import com.kravein.connect.chat.ConfirmStatus
import com.kravein.connect.chat.Danger
import com.kravein.connect.chat.Link
import com.kravein.connect.chat.Markdown
import com.kravein.connect.chat.Span
import com.kravein.connect.chat.StatusKind
import com.kravein.connect.chat.TimelineRow
import com.kravein.connect.chat.ToolCallItem
import com.kravein.connect.chat.ToolStatus
import com.kravein.connect.ui.theme.GradientButton
import com.kravein.connect.ui.theme.KraveinTheme

private val RailWidth = 28.dp
private val NodeY = 14.dp

/** Animations off when the user turned the system animation scale to 0 ("remove animations"). */
@Composable
private fun reducedMotion(): Boolean {
    val resolver = LocalContext.current.contentResolver
    return remember { Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }
}

@Composable
fun ChatScreen(
    state: ChatState,
    prefill: String,
    onPrefillUsed: () -> Unit,
    onSend: (String) -> Unit,
    onStop: () -> Unit,
    onNew: () -> Unit,
    onConfirm: (String, Boolean) -> Unit,
    onDismissLoose: (String) -> Unit,
    onRetry: () -> Unit,
    onPairAgain: () -> Unit,
) {
    val rows = remember(state.messages) { ChatTimeline.rows(state.messages) }
    val listState = rememberLazyListState()
    var draft by remember { mutableStateOf("") }
    val reduced = reducedMotion()

    // A shared text pre-fills the composer; it is never sent by itself.
    LaunchedEffect(prefill) {
        if (prefill.isNotEmpty()) {
            draft = prefill
            onPrefillUsed()
        }
    }
    // Follow the newest node while the turn streams in.
    LaunchedEffect(rows.size, rows.lastOrNull()?.key) {
        if (rows.isNotEmpty()) listState.animateScrollToItem(rows.lastIndex)
    }

    Column(Modifier.fillMaxSize().imePadding()) {
        ChatHeader(state, onNew, onRetry)
        if (state.link == Link.UNAUTHORIZED) {
            Column(Modifier.fillMaxWidth().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("This phone is no longer paired", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Text("Kravein on your PC refused this device. Pair it again to continue.")
                GradientButton(onClick = onPairAgain, modifier = Modifier.fillMaxWidth()) { Text("Pair again") }
            }
            return@Column
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (rows.isEmpty()) {
                EmptyChat(state)
            } else {
                LazyColumn(state = listState, modifier = Modifier.fillMaxSize(), contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 12.dp, vertical = 8.dp)) {
                    itemsIndexed(rows, key = { _, row -> row.key }) { index, row ->
                        TimelineItem(
                            row = row,
                            first = index == 0,
                            last = index == rows.lastIndex,
                            reduced = reduced,
                            onConfirm = onConfirm,
                            modifier = if (reduced) Modifier else // No fade-out: the "Working…" row is replaced by the answer in the same spot, and its fading copy
                            // stayed drawn under the answer on the device.
                            Modifier.animateItem(fadeOutSpec = null),
                        )
                    }
                }
            }
            // Confirmations of voice/panel turns have no timeline to live in; they are still never dropped.
            Column(Modifier.align(Alignment.BottomCenter).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                state.looseConfirms.forEach { c ->
                    LaunchedEffect(c.confirmId, c.status) {
                        if (c.status != ConfirmStatus.PENDING) {
                            kotlinx.coroutines.delay(4000)
                            onDismissLoose(c.confirmId)
                        }
                    }
                    ConfirmCard(c, onConfirm)
                }
            }
        }
        Composer(draft, { draft = it }, state, onSend = { onSend(draft); draft = "" }, onStop = onStop)
    }
}

@Composable
private fun ChatHeader(state: ChatState, onNew: () -> Unit, onRetry: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        val (label, color) = when (state.link) {
            Link.OPEN -> "Connected" to MaterialTheme.colorScheme.primary
            Link.CONNECTING -> "Connecting…" to MaterialTheme.colorScheme.onSurfaceVariant
            Link.RECONNECTING -> "Reconnecting…" to KraveinTheme.colors.moderate
            Link.OFFLINE -> "Offline — retrying" to KraveinTheme.colors.destructive
            Link.UNAUTHORIZED -> "Not paired" to KraveinTheme.colors.destructive
        }
        Box(Modifier.size(8.dp).background(color, CircleShape))
        Spacer(Modifier.width(8.dp))
        Column(Modifier.weight(1f)) {
            Text(state.assistantName, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (state.link == Link.OFFLINE || state.link == Link.RECONNECTING) TextButton(onClick = onRetry) { Text("Retry now") }
        TextButton(onClick = onNew, enabled = !state.busy && state.messages.isNotEmpty()) { Text("New") }
    }
}

@Composable
private fun EmptyChat(state: ChatState) {
    Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Text("Ask ${state.assistantName} anything", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(6.dp))
        Text(
            "It can use your PC and this phone. Risky steps always ask you first.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun Composer(draft: String, onDraft: (String) -> Unit, state: ChatState, onSend: () -> Unit, onStop: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        OutlinedTextField(
            value = draft,
            onValueChange = onDraft,
            modifier = Modifier.weight(1f),
            placeholder = { Text("Message ${state.assistantName}") },
            maxLines = 5,
            shape = RoundedCornerShape(18.dp),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { if (draft.isNotBlank() && !state.busy) onSend() }),
        )
        if (state.busy) {
            OutlinedButton(onClick = onStop, enabled = !state.cancelling) { Text(if (state.cancelling) "Stopping…" else "Stop") }
        } else {
            IconButton(onClick = onSend, enabled = draft.isNotBlank()) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", tint = if (draft.isNotBlank()) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** One node plus the line that joins it to its neighbours. */
@Composable
private fun TimelineItem(row: TimelineRow, first: Boolean, last: Boolean, reduced: Boolean, onConfirm: (String, Boolean) -> Unit, modifier: Modifier) {
    val line = MaterialTheme.colorScheme.outline
    val nodeColor = nodeColor(row)
    val pulse = if (row is TimelineRow.Working || (row is TimelineRow.Tool && row.tool.status == ToolStatus.RUNNING) || (row is TimelineRow.Thinking && row.live)) {
        if (reduced) 1f else {
            val t = rememberInfiniteTransition(label = "pulse")
            t.animateFloat(0.35f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "alpha").value
        }
    } else 1f
    Row(modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
        Box(
            Modifier.width(RailWidth).fillMaxSize().drawBehind {
                val x = size.width / 2
                val y = NodeY.toPx()
                // The line runs through the node: from the previous node's side down to the next one.
                if (!first) drawLine(line, Offset(x, 0f), Offset(x, y), strokeWidth = 2.dp.toPx())
                if (!last) drawLine(line, Offset(x, y), Offset(x, size.height), strokeWidth = 2.dp.toPx())
                drawCircle(nodeColor.copy(alpha = 0.25f * pulse), radius = 8.dp.toPx(), center = Offset(x, y))
                drawCircle(nodeColor.copy(alpha = pulse), radius = 4.5.dp.toPx(), center = Offset(x, y))
            },
        )
        Box(Modifier.weight(1f).padding(bottom = 10.dp)) { RowContent(row, onConfirm) }
    }
}

@Composable
private fun nodeColor(row: TimelineRow): Color = when (row) {
    is TimelineRow.Request -> MaterialTheme.colorScheme.onSurfaceVariant
    is TimelineRow.Thinking, is TimelineRow.Working -> KraveinTheme.colors.accent2
    is TimelineRow.Tool -> when (row.tool.status) {
        ToolStatus.RUNNING -> MaterialTheme.colorScheme.primary
        ToolStatus.DONE -> MaterialTheme.colorScheme.primary
        ToolStatus.FAILED -> KraveinTheme.colors.destructive
        ToolStatus.UNKNOWN -> KraveinTheme.colors.moderate
    }
    is TimelineRow.Confirm -> dangerColor(row.confirm.danger)
    is TimelineRow.Answer -> MaterialTheme.colorScheme.primary
    is TimelineRow.Status -> when (row.kind) {
        StatusKind.ERROR -> KraveinTheme.colors.destructive
        StatusKind.STOPPED, StatusKind.LOST -> KraveinTheme.colors.moderate
    }
}

@Composable
private fun dangerColor(d: Danger): Color = when (d) {
    Danger.SAFE -> MaterialTheme.colorScheme.primary
    Danger.MODERATE -> KraveinTheme.colors.moderate
    Danger.DESTRUCTIVE -> KraveinTheme.colors.destructive
    Danger.CRITICAL -> KraveinTheme.colors.critical
}

@Composable
private fun RowContent(row: TimelineRow, onConfirm: (String, Boolean) -> Unit) {
    when (row) {
        is TimelineRow.Request -> Text(row.text, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 4.dp))
        is TimelineRow.Thinking -> Text(
            row.text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = if (row.live) 6 else 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp),
        )
        is TimelineRow.Working -> Text("Working…", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
        is TimelineRow.Tool -> ToolCard(row.tool)
        is TimelineRow.Confirm -> ConfirmCard(row.confirm, onConfirm)
        is TimelineRow.Answer -> MarkdownText(row.text, Modifier.padding(top = 2.dp))
        is TimelineRow.Status -> Text(
            row.text, style = MaterialTheme.typography.bodyMedium,
            color = if (row.kind == StatusKind.ERROR) KraveinTheme.colors.destructive else KraveinTheme.colors.moderate,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

@Composable
private fun ToolCard(tool: ToolCallItem) {
    var open by remember { mutableStateOf(false) }
    val state = when (tool.status) {
        ToolStatus.RUNNING -> "running"
        ToolStatus.DONE -> if (open) "hide" else "show"
        ToolStatus.FAILED -> "failed"
        ToolStatus.UNKNOWN -> "unknown"
    }
    Column(
        Modifier.fillMaxWidth()
            .background(KraveinTheme.colors.glass, RoundedCornerShape(12.dp))
            .border(BorderStroke(1.dp, KraveinTheme.colors.glassBorder), RoundedCornerShape(12.dp))
            .clickable(enabled = tool.result != null) { open = !open }
            .padding(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(tool.name, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(state, style = MaterialTheme.typography.labelSmall, color = if (tool.status == ToolStatus.UNKNOWN) KraveinTheme.colors.moderate else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (tool.display.isNotBlank()) {
            Text(tool.display, style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = if (open) 12 else 2, overflow = TextOverflow.Ellipsis)
        }
        AnimatedVisibility(visible = open && tool.result != null || tool.status == ToolStatus.UNKNOWN) {
            Text(
                (tool.result ?: "").take(4000),
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                modifier = Modifier.padding(top = 6.dp).fillMaxWidth().horizontalScroll(rememberScrollState()),
            )
        }
    }
}

@Composable
private fun ConfirmCard(c: ConfirmItem, onConfirm: (String, Boolean) -> Unit) {
    val color = dangerColor(c.danger)
    Column(
        Modifier.fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface, RoundedCornerShape(12.dp))
            .border(BorderStroke(1.5.dp, color), RoundedCornerShape(12.dp))
            .padding(12.dp)
            .semantics { contentDescription = "Confirmation: ${c.action}" },
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(c.action, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        if (c.detail.isNotBlank()) Text(c.detail.take(1000), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        when (c.status) {
            ConfirmStatus.PENDING -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                GradientButton(onClick = { onConfirm(c.confirmId, true) }, modifier = Modifier.weight(1f)) { Text("Approve") }
                OutlinedButton(onClick = { onConfirm(c.confirmId, false) }, modifier = Modifier.weight(1f)) { Text("Decline") }
            }
            else -> Text(outcomeLabel(c), style = MaterialTheme.typography.labelMedium, color = color)
        }
    }
}

/** The line a closed card shows: "Approved on phone", "Declined", "Timed out". Same words as the web card. */
internal fun outcomeLabel(c: ConfirmItem): String {
    val where = if (c.by == "phone") " on phone" else ""
    return when (c.status) {
        ConfirmStatus.APPROVED -> "Approved$where"
        ConfirmStatus.DENIED -> if (c.by == "phone") "Declined on phone" else "Declined"
        ConfirmStatus.EXPIRED -> "Timed out — treated as denied"
        ConfirmStatus.PENDING -> ""
    }
}

@Composable
private fun MarkdownText(text: String, modifier: Modifier = Modifier) {
    val blocks = remember(text) { Markdown.parse(text) }
    val context = LocalContext.current
    val linkColor = MaterialTheme.colorScheme.primary
    val codeBg = KraveinTheme.colors.glassStrong
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for (b in blocks) when (b) {
            is Block.Paragraph -> Text(styled(b.spans, linkColor, codeBg, context), style = MaterialTheme.typography.bodyLarge)
            is Block.Bullet -> Row {
                Text("•  ", style = MaterialTheme.typography.bodyLarge)
                Text(styled(b.spans, linkColor, codeBg, context), style = MaterialTheme.typography.bodyLarge)
            }
            is Block.CodeBlock -> Text(
                b.text,
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                modifier = Modifier.fillMaxWidth().background(codeBg, RoundedCornerShape(8.dp)).padding(8.dp).horizontalScroll(rememberScrollState()),
            )
        }
    }
}

private fun styled(spans: List<Span>, linkColor: Color, codeBg: Color, context: android.content.Context): AnnotatedString = buildAnnotatedString {
    for (s in spans) when (s) {
        is Span.Text -> append(s.text)
        is Span.Bold -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(s.text) }
        is Span.Code -> withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = codeBg)) { append(s.text) }
        is Span.Link -> withLink(
            LinkAnnotation.Clickable(
                tag = s.url,
                styles = TextLinkStyles(SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline)),
            ) { openExternally(context, s.url) },
        ) { append(s.label) }
    }
}

/** Links go to the browser, never inside the app; only http(s) ever reaches here (see Markdown.isWebUrl). */
private fun openExternally(context: android.content.Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) {
        // No browser installed: nothing to open, and nothing worth crashing for.
    }
}
