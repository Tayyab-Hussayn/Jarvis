package com.kravein.connect.bubble

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import android.view.animation.LinearInterpolator
import com.kravein.connect.chat.VoicePhase
import com.kravein.connect.ui.theme.KraveinTokens

/**
 * The floating agent orb. Plain Views on purpose: a Compose tree in a service needs its own lifecycle and saved-state
 * owners, and this window is one circle and four small controls. It exists only while [BubblePolicy] says so — the
 * service creates it when voice is live and the app is not in front and calls [remove] the moment either stops being
 * true — and it animates only while attached, so a hidden bubble costs nothing.
 */
class BubbleOverlay(
    private val context: Context,
    private val onToggleMute: () -> Unit,
    private val onStop: () -> Unit,
    private val onOpen: () -> Unit,
) {
    private val wm = context.getSystemService(WindowManager::class.java)
    private val palette = KraveinTokens.Dark
    private val density = context.resources.displayMetrics.density

    private val orb = OrbView(context, palette.accent.toInt(), palette.accent2.toInt(), palette.bg.toInt())
    private val lastLine = TextView(context).apply {
        setTextColor(palette.text.toInt())
        textSize = 13f
        maxLines = 3
        ellipsize = android.text.TextUtils.TruncateAt.END
    }
    private val status = TextView(context).apply {
        setTextColor(palette.textMuted.toInt())
        textSize = 11f
    }
    private val muteButton = pill("Mute") { onToggleMute() }
    private val panel = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        visibility = View.GONE
        val pad = dp(12)
        setPadding(pad, pad, pad, pad)
        background = GradientDrawable().apply {
            setColor(palette.surfaceRaised.toInt())
            cornerRadius = dp(KraveinTokens.RADIUS_MD).toFloat()
            setStroke(dp(1), palette.glassBorder.toInt())
        }
        addView(status)
        addView(lastLine, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(4); bottomMargin = dp(10) })
        addView(
            LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                addView(muteButton, LinearLayout.LayoutParams(0, dp(40), 1f))
                addView(pill("Stop") { onStop() }, LinearLayout.LayoutParams(0, dp(40), 1f).apply { marginStart = dp(8) })
                addView(pill("Open") { onOpen() }, LinearLayout.LayoutParams(0, dp(40), 1f).apply { marginStart = dp(8) })
            },
        )
    }
    private val root = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        addView(orb, LinearLayout.LayoutParams(dp(ORB_DP), dp(ORB_DP)))
        addView(panel, LinearLayout.LayoutParams(dp(PANEL_DP), -2).apply { topMargin = dp(8) })
    }

    private val params = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        // Never takes focus (the app underneath keeps its keyboard) and never blocks touches outside itself.
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        x = screenWidth() - dp(ORB_DP) - dp(MARGIN_DP)
        y = screenHeight() / 3
    }
    private var atRight = true
    private var attached = false

    fun update(phase: VoicePhase, muted: Boolean, reconnecting: Boolean, line: String, statusText: String) {
        orb.setState(phase, muted, reconnecting)
        orb.contentDescription = "Kravein voice, $statusText. Tap for controls."
        status.text = statusText
        lastLine.text = line.ifBlank { "Nothing said yet." }
        muteButton.text = if (muted) "Unmute" else "Mute"
        if (!attached) {
            try {
                wm.addView(root, params)
                attached = true
            } catch (_: Exception) {
                // The permission was revoked between the check and the add: no bubble, and nothing else breaks.
            }
        }
    }

    fun remove() {
        if (!attached) return
        attached = false
        orb.stopAnimating()
        try {
            wm.removeView(root)
        } catch (_: IllegalArgumentException) {
            // Already gone.
        }
    }

    init {
        val slop = ViewConfiguration.get(context).scaledTouchSlop.toFloat()
        var downX = 0f
        var downY = 0f
        var startX = 0
        var startY = 0
        var dragging = false
        orb.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = e.rawX; downY = e.rawY; startX = params.x; startY = params.y; dragging = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX
                    val dy = e.rawY - downY
                    if (!BubbleGeometry.isTap(dx, dy, slop)) dragging = true
                    if (dragging && attached) {
                        params.x = startX + dx.toInt()
                        params.y = BubbleGeometry.clampY(startY + dy.toInt(), root.height, screenHeight(), dp(TOP_DP), dp(BOTTOM_DP))
                        wm.updateViewLayout(root, params)
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (dragging) snap() else togglePanel()
                    true
                }
                else -> false
            }
        }
    }

    private fun togglePanel() {
        panel.visibility = if (panel.visibility == View.VISIBLE) View.GONE else View.VISIBLE
        // The window grows to the panel's width; keep it hugging the edge it sits on.
        root.post { snap() }
    }

    private fun snap() {
        if (!attached) return
        val width = root.width.coerceAtLeast(dp(ORB_DP))
        val target = BubbleGeometry.snapX(params.x, width, screenWidth(), dp(MARGIN_DP))
        atRight = target > dp(MARGIN_DP)
        params.x = target
        params.y = BubbleGeometry.clampY(params.y, root.height, screenHeight(), dp(TOP_DP), dp(BOTTOM_DP))
        // The orb stays on the side of the screen edge, the panel opens towards the middle.
        root.gravity = if (atRight) Gravity.END else Gravity.START
        wm.updateViewLayout(root, params)
    }

    private fun pill(text: String, click: () -> Unit) = TextView(context).apply {
        this.text = text
        gravity = Gravity.CENTER
        textSize = 13f
        setTextColor(palette.text.toInt())
        background = GradientDrawable().apply {
            setColor(palette.glassStrong.toInt())
            cornerRadius = dp(KraveinTokens.RADIUS_SM).toFloat()
            setStroke(dp(1), palette.glassBorder.toInt())
        }
        setOnClickListener { click() }
    }

    private fun dp(v: Int) = (v * density).toInt()
    private fun screenWidth() = context.resources.displayMetrics.widthPixels
    private fun screenHeight() = context.resources.displayMetrics.heightPixels

    private companion object {
        const val ORB_DP = 56
        const val PANEL_DP = 260
        const val MARGIN_DP = 8
        const val TOP_DP = 40
        const val BOTTOM_DP = 56
    }
}

/** The orb: aurora gradient disc, with a ring per state. Animates only while attached to a window and not muted. */
private class OrbView(context: Context, private val accent: Int, private val accent2: Int, private val ground: Int) : View(context) {
    private var phase = VoicePhase.IDLE
    private var muted = false
    private var reconnecting = false
    private var t = 0f
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val arc = RectF()
    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener { t = it.animatedValue as Float; invalidate() }
    }

    fun setState(phase: VoicePhase, muted: Boolean, reconnecting: Boolean) {
        this.phase = phase
        this.muted = muted
        this.reconnecting = reconnecting
        animator.duration = when {
            phase == VoicePhase.SPEAKING -> 800L
            phase == VoicePhase.THINKING -> 1500L
            else -> 2600L
        }
        // A muted orb is still: nothing is being listened to, so nothing moves.
        if (muted || !isAttachedToWindow) animator.cancel() else if (!animator.isRunning) animator.start()
        invalidate()
    }

    fun stopAnimating() = animator.cancel()

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (!muted && !animator.isRunning) animator.start()
    }

    override fun onDetachedFromWindow() {
        animator.cancel()
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val base = width * 0.34f
        val pulse = if (muted) 0f else when (phase) {
            VoicePhase.SPEAKING -> (kotlin.math.sin(t * 2 * Math.PI).toFloat() + 1f) / 2f
            VoicePhase.LISTENING -> t
            else -> (kotlin.math.sin(t * 2 * Math.PI).toFloat() + 1f) / 4f
        }
        val dim = if (muted || reconnecting) 0.45f else 1f

        // Expanding ring: listening breathes outwards, speaking pulses harder.
        if (!muted) {
            ring.strokeWidth = width * 0.03f
            ring.color = withAlpha(accent, ((1f - pulse) * 0.55f * dim * 255).toInt())
            canvas.drawCircle(cx, cy, base + pulse * width * (if (phase == VoicePhase.SPEAKING) 0.16f else 0.14f), ring)
        }
        fill.shader = RadialGradient(
            cx - base * 0.3f, cy - base * 0.3f, base * 1.4f,
            intArrayOf(withAlpha(accent2, (dim * 255).toInt()), withAlpha(accent, (dim * 255).toInt()), withAlpha(ground, (dim * 255).toInt())),
            floatArrayOf(0f, 0.65f, 1f), Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, base + if (phase == VoicePhase.SPEAKING && !muted) pulse * width * 0.04f else 0f, fill)

        if (phase == VoicePhase.THINKING && !muted) {
            ring.strokeWidth = width * 0.045f
            ring.color = withAlpha(accent2, 230)
            arc.set(cx - base - width * 0.07f, cy - base - width * 0.07f, cx + base + width * 0.07f, cy + base + width * 0.07f)
            canvas.drawArc(arc, t * 360f, 90f, false, ring)
        }
        if (muted) {
            ring.strokeWidth = width * 0.05f
            ring.color = withAlpha(0xFFFFFFFF.toInt(), 200)
            canvas.drawLine(cx - base * 0.6f, cy + base * 0.6f, cx + base * 0.6f, cy - base * 0.6f, ring)
        }
    }

    private fun withAlpha(color: Int, alpha: Int): Int = (color and 0x00FFFFFF) or (alpha.coerceIn(0, 255) shl 24)
}
