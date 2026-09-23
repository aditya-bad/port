package com.livedeploy.app.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.CreationExtras
import com.livedeploy.app.LiveDeployApplication
import com.livedeploy.app.data.DeploymentsRepository
import com.livedeploy.app.data.SettingsStore

/**
 * No Hilt/Koin — this app is small enough (a handful of screens, one
 * repository, one settings store) that a DI framework would add setup
 * surface without buying much: everything any ViewModel needs is exactly
 * "the app's own SettingsStore" and "a Repository built from it", both
 * cheap to construct directly here.
 */
class AppViewModelFactory(private val app: LiveDeployApplication) : ViewModelProvider.Factory {
    private val repository by lazy { DeploymentsRepository(app.settingsStore) }
    val settingsStore: SettingsStore get() = app.settingsStore

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T {
        return when {
            modelClass.isAssignableFrom(com.livedeploy.app.ui.setup.SetupViewModel::class.java) ->
                com.livedeploy.app.ui.setup.SetupViewModel(repository, settingsStore) as T
            modelClass.isAssignableFrom(com.livedeploy.app.ui.dashboard.DashboardViewModel::class.java) ->
                com.livedeploy.app.ui.dashboard.DashboardViewModel(repository) as T
            modelClass.isAssignableFrom(com.livedeploy.app.ui.deployments.DeploymentsViewModel::class.java) ->
                com.livedeploy.app.ui.deployments.DeploymentsViewModel(repository) as T
            modelClass.isAssignableFrom(com.livedeploy.app.ui.deployments.DeploymentDetailViewModel::class.java) ->
                com.livedeploy.app.ui.deployments.DeploymentDetailViewModel(repository) as T
            else -> throw IllegalArgumentException("Unknown ViewModel class: $modelClass")
        }
    }
}
