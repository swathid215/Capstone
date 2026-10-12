import React, { useEffect, useState } from 'react'
import { RefreshCcw, Moon, Sun, FolderPlus, ArrowLeft, Trash2 } from 'lucide-react'
import TrafficReport from './components/TrafficReport'
import MapCommandCenter from './components/MapCommandCenter'
import AuthScreen from './components/AuthScreen'
import { apiUrl } from './api'

function ThemeToggle({ darkMode, onToggle }) {
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={onToggle}
      aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
      title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      <span>{darkMode ? 'Light' : 'Dark'}</span>
    </button>
  )
}

export default function App() {
  const params = new URLSearchParams(window.location.search);
  const isReportView = params.get('view') === 'report';

  const [screen, setScreen] = useState(isReportView ? 'report' : 'projects');
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [activeProject, setActiveProject] = useState(null);
  const [projects, setProjects] = useState([]);
  const [reportData, setReportData] = useState(null);
  const [compareVariantArtifacts, setCompareVariantArtifacts] = useState(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [darkMode, setDarkMode] = useState(() => {
    try {
      return localStorage.getItem('uidt-theme') === 'dark';
    } catch (e) {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem('uidt-theme', darkMode ? 'dark' : 'light');
    } catch (e) {
      // Theme preference is optional; the UI still works if storage is unavailable.
    }
  }, [darkMode]);

  const refreshProjects = async () => {
    const response = await fetch(apiUrl('/api/projects'), { credentials: 'include' });
    if (response.status === 401) throw new Error('Authentication required');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Unable to load projects');
    setProjects(data.projects || []);
  };

  useEffect(() => {
    fetch(apiUrl('/api/auth/me'), { credentials: 'include' })
      .then((res) => res.ok ? res.json() : Promise.reject())
      .then((data) => { setUser(data.user); return refreshProjects(); })
      .catch(() => { setUser(null); setProjects([]); })
      .finally(() => setAuthLoading(false));
  }, []);

  const handleGenerateReport = (data) => {
    setReportData(data);
    setIsAnalyzing(false);
    setScreen('report');
  };

  const handleResetReport = () => {
    setReportData(null);
    setScreen('map');
  };

  const handleViewReport = () => {
    if (!reportData && activeProject?.report && Object.keys(activeProject.report).length) {
      setReportData(activeProject.report);
    }
    setScreen('report');
  };

  const handleCreateProject = async () => {
    if (projects.length >= 5) {
      window.alert('You can have a maximum of 5 projects. Delete an existing project to create a new one.');
      return;
    }
    const name = window.prompt('Project name');
    if (!name) return;
    try {
      const res = await fetch(apiUrl('/api/projects'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Create project failed');
      const nextProject = data.project;
      setCompareVariantArtifacts(null);
      setActiveProject(nextProject);
      setReportData(null);
      setScreen('map');
      await refreshProjects();
    } catch (e) {
      window.alert(e.message || 'Unable to create project');
    }
  };

  const handleOpenProject = async (project) => {
    try {
      const response = await fetch(apiUrl(`/api/projects/${project.id}`), { credentials: 'include' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Unable to open project');
      setCompareVariantArtifacts(null);
      setActiveProject(data.project);
      setReportData(data.project.report && Object.keys(data.project.report).length ? data.project.report : null);
      setScreen('map');
    } catch (error) { window.alert(error.message); }
  };

  const handleRenameActiveProject = async () => {
    if (!activeProject) return;
    const nextName = window.prompt('Rename project', activeProject.name || '');
    if (!nextName) return;
    try {
      const res = await fetch(apiUrl(`/api/projects/${activeProject.id}`), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: nextName.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Rename failed');
      setActiveProject(data.project);
      await refreshProjects();
    } catch (e) {
      window.alert(e.message || 'Unable to rename project');
    }
  };

  const handleDeleteProject = async (project) => {
    if (!window.confirm(`Delete "${project.name}"? This cannot be undone.`)) return;
    try {
      const response = await fetch(apiUrl(`/api/projects/${project.id}`), { method: 'DELETE', credentials: 'include' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Delete failed');
      if (activeProject?.id === project.id) {
        setCompareVariantArtifacts(null);
        setActiveProject(null);
        setReportData(null);
        setScreen('projects');
      }
      await refreshProjects();
    } catch (error) { window.alert(error.message); }
  };

  const handleAuthenticated = (nextUser) => {
    setUser(nextUser);
    setScreen('projects');
    refreshProjects().catch(() => setProjects([]));
  };

  const handleLogout = async () => {
    await fetch(apiUrl('/api/auth/logout'), { method: 'POST', credentials: 'include' });
    setUser(null); setProjects([]); setActiveProject(null); setReportData(null); setCompareVariantArtifacts(null); setScreen('projects');
  };

  if (authLoading) return <div className="auth-loading">Loading secure workspace...</div>;
  if (!user) return <AuthScreen onAuthenticated={handleAuthenticated} />;

  return (
    <div className={`app-shell h-screen flex flex-col ${darkMode ? 'theme-dark' : ''}`}>
      <header className="app-header flex items-center justify-between gap-4 z-[2000]">
        <div className="app-brand">
          <div className="app-brand-mark">U</div>
          <div>
            <div className="app-brand-name">UIDT</div>
            <div className="app-brand-subtitle">Urban Infrastructure Digital Twin</div>
          </div>
        </div>
        <nav className="app-nav flex gap-2 items-center" aria-label="Primary navigation">
        <button
          onClick={() => setScreen('projects')}
          className={`app-nav-button ${screen === 'projects' ? 'is-active' : ''}`}
        >
          Projects
        </button>
        {activeProject && <button onClick={() => setScreen('map')} className={`app-nav-button ${screen === 'map' ? 'is-active' : ''}`}>Command Center</button>}
        {activeProject && reportData && <button onClick={handleViewReport} className={`app-nav-button ${screen === 'report' ? 'is-active' : ''}`}>Traffic Report</button>}
        </nav>
        <div className="app-header-tools">
          <button
            type="button"
            className="theme-toggle"
            onClick={() => window.location.reload()}
            aria-label="Refresh page"
            title="Refresh page"
          >
            <RefreshCcw className="h-4 w-4" />
            <span>Refresh</span>
          </button>
          <ThemeToggle darkMode={darkMode} onToggle={() => setDarkMode((value) => !value)} />
          {activeProject && (
            <span className="active-project-chip">
              <span className="active-project-label">Project:</span>
              <span className="active-project-name">{activeProject.name}</span>
              <button type="button" className="rename-project-button" onClick={handleRenameActiveProject}>Rename</button>
              <button type="button" className="back-project-button" onClick={() => setScreen('projects')}><ArrowLeft size={14} /> Back to Projects</button>
            </span>
          )}
          <button type="button" className="back-project-button" onClick={handleLogout}>Sign out</button>
          {isAnalyzing && (
            <span className="analysis-pill animate-pulse">
              <span className="analysis-pill-dot" /> Analysis running · up to 90 seconds
            </span>
          )}
        </div>
      </header>

      <div
        className={`app-main flex-1 relative min-h-0 ${
          screen === 'map' ? 'overflow-hidden' : 'overflow-y-auto'
        }`}
      >
        <div className={screen === 'projects' ? 'h-full' : 'hidden'}>
          <div className="projects-view p-8 bg-slate-50 min-h-full">
            <div className="flex items-center justify-between gap-4 mb-6">
              <div>
                <div className="text-xs font-black uppercase tracking-wide text-blue-600">UIDT Project Hub</div>
                <h1 className="text-3xl font-black text-slate-900 mt-2">Projects</h1>
                <p className="text-sm text-slate-500 mt-2">{projects.length} / 5 Projects</p>
              </div>
              <button type="button" className="theme-toggle" onClick={handleCreateProject}>
                <FolderPlus className="h-4 w-4" />
                <span>New project</span>
              </button>
              {projects.length >= 5 && <p className="project-limit-message">You can have a maximum of 5 projects. Delete an existing project to create a new one.</p>}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {projects.length === 0 && (
                <div className="col-span-full rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
                  <p className="text-sm font-bold text-slate-600">No projects yet</p>
                  <button type="button" className="theme-toggle mt-4" onClick={handleCreateProject}>
                    <FolderPlus className="h-4 w-4" />
                    <span>Create one</span>
                  </button>
                </div>
              )}
              {projects.map((project) => (
                <div key={project.id} className="project-card text-left">
                  <div className="project-card-top">
                    <span className="project-card-name">{project.name}</span>
                  </div>
                  <div className="project-card-meta">
                    <div className="project-card-actions"><button type="button" className="project-card-action" onClick={() => handleOpenProject(project)}>Open</button><button type="button" className="project-card-action danger" onClick={() => handleDeleteProject(project)}><Trash2 size={14} /> Delete</button></div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className={screen === 'map' ? 'h-full' : 'hidden'}>
          <MapCommandCenter
            key={activeProject?.id || 'no-project'}
            onGenerate={handleGenerateReport}
            onAnalyzingChange={setIsAnalyzing}
            onReset={handleResetReport}
            onViewReport={handleViewReport}
            activeProject={activeProject}
            projectId={activeProject?.id}
            compareVariantArtifacts={compareVariantArtifacts}
            userEmail={user?.email || ''}
            onProjectNameChange={handleRenameActiveProject}
            onProjectBack={() => setScreen('projects')}
          />
        </div>
        <div className={screen === 'report' ? 'h-full' : 'hidden'}>
          <TrafficReport
            data={reportData}
            darkMode={darkMode}
            onToggleDarkMode={() => setDarkMode((value) => !value)}
            onCompareVariantSelect={setCompareVariantArtifacts}
          />
        </div>

        {isAnalyzing && (
          <div className="analysis-overlay absolute inset-0 flex items-center justify-center z-[1500]">
            <div className="analysis-modal max-w-md text-center">
              <div className="analysis-spinner w-12 h-12 rounded-full animate-spin mx-auto mb-4" />
              <p className="eyebrow">UIDT ANALYSIS</p>
              <h2 className="text-xl font-bold text-gray-900">Building your scenario</h2>
              <p className="text-gray-600 mt-2 text-sm leading-relaxed">
                Loading OSM data, checking building collisions, modifying the road graph,
                simulating traffic, and generating maps…
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
