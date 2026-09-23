package com.livedeploy.app.ui.nav

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.List
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.livedeploy.app.ui.AppViewModelFactory
import com.livedeploy.app.ui.dashboard.DashboardScreen
import com.livedeploy.app.ui.deployments.DeploymentDetailScreen
import com.livedeploy.app.ui.deployments.DeploymentsScreen
import com.livedeploy.app.ui.setup.SetupScreen

private object Routes {
    const val SETUP = "setup"
    const val DASHBOARD = "dashboard"
    const val DEPLOYMENTS = "deployments"
    const val DEPLOYMENT_DETAIL = "deployments/{id}"
    fun deploymentDetail(id: String) = "deployments/$id"
}

private data class BottomTab(val route: String, val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)

private val bottomTabs = listOf(
    BottomTab(Routes.DASHBOARD, "Dashboard", Icons.Default.Dashboard),
    BottomTab(Routes.DEPLOYMENTS, "Deployments", Icons.Default.List),
)

/**
 * Start destination is decided once, synchronously, from whatever
 * SettingsStore already has saved (see MainActivity) — not re-checked on
 * every recomposition — so a configured user never sees a Setup-screen
 * flash before landing on Dashboard.
 */
@Composable
fun LiveDeployNavGraph(factory: AppViewModelFactory, startAtSetup: Boolean) {
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination

    val showBottomBar = currentRoute?.hierarchy?.any { dest ->
        bottomTabs.any { it.route == dest.route }
    } == true

    Scaffold(
        bottomBar = {
            if (showBottomBar) {
                NavigationBar {
                    bottomTabs.forEach { tab ->
                        val selected = currentRoute?.hierarchy?.any { it.route == tab.route } == true
                        NavigationBarItem(
                            selected = selected,
                            onClick = {
                                navController.navigate(tab.route) {
                                    popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(tab.icon, contentDescription = tab.label) },
                            label = { Text(tab.label) },
                        )
                    }
                }
            }
        },
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = if (startAtSetup) Routes.SETUP else Routes.DASHBOARD,
            modifier = androidx.compose.ui.Modifier.padding(innerPadding),
        ) {
            composable(Routes.SETUP) {
                SetupScreen(
                    factory = factory,
                    onConnected = {
                        navController.navigate(Routes.DASHBOARD) {
                            popUpTo(Routes.SETUP) { inclusive = true }
                        }
                    },
                )
            }
            composable(Routes.DASHBOARD) {
                DashboardScreen(factory = factory)
            }
            composable(Routes.DEPLOYMENTS) {
                DeploymentsScreen(
                    factory = factory,
                    onOpenDeployment = { id -> navController.navigate(Routes.deploymentDetail(id)) },
                )
            }
            composable(
                route = Routes.DEPLOYMENT_DETAIL,
                arguments = listOf(navArgument("id") { type = androidx.navigation.NavType.StringType }),
            ) { backStackEntry ->
                val id = backStackEntry.arguments?.getString("id") ?: return@composable
                DeploymentDetailScreen(
                    factory = factory,
                    deploymentId = id,
                    onBack = { navController.popBackStack() },
                )
            }
        }
    }
}
