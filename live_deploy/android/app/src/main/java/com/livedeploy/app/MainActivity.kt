package com.livedeploy.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.livedeploy.app.ui.AppViewModelFactory
import com.livedeploy.app.ui.nav.LiveDeployNavGraph
import com.livedeploy.app.ui.theme.LiveDeployTheme
import kotlinx.coroutines.flow.first

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val app = application as LiveDeployApplication
        val factory = AppViewModelFactory(app)

        setContent {
            LiveDeployTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    // One-time, async check of whether Setup has already
                    // been completed — null means "still checking", not
                    // "not configured", so a returning user never sees a
                    // Setup-screen flash before landing on Dashboard (see
                    // NavGraph's own comment on why this is decided once,
                    // not re-checked on every recomposition).
                    val startAtSetup by produceState<Boolean?>(initialValue = null) {
                        value = app.settingsStore.settings.first() == null
                    }
                    when (val resolved = startAtSetup) {
                        null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator()
                        }
                        else -> LiveDeployNavGraph(factory = factory, startAtSetup = resolved)
                    }
                }
            }
        }
    }
}
