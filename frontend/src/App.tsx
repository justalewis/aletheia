import { useEffect, useRef } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import { PackContext, useAsync } from "./hooks";
import Home from "./pages/Home";
import FocusPage from "./pages/FocusPage";
import ArcDetail from "./pages/ArcDetail";
import Catalog from "./pages/Catalog";
import About from "./pages/About";
import Outcomes from "./pages/Outcomes";
import NotFound from "./pages/NotFound";
import Builder from "./builder/Builder";
import StartCourse from "./pages/StartCourse";
import Feedback from "./pages/Feedback";
import NewProgram from "./programs/NewProgram";
import ProgramPage from "./programs/ProgramPage";
import ProgramAdmin from "./programs/ProgramAdmin";

export default function App() {
  const pack = useAsync((signal) => api.pack(signal), []);
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const firstRender = useRef(true);

  // Move focus to the main region on route changes so keyboard and screen reader users start at the top.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    // Saving a new course swaps /build/new for /build/:id; stay where the instructor was.
    if ((location.state as { preserveFocus?: boolean } | null)?.preserveFocus) return;
    window.scrollTo(0, 0);
    mainRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  return (
    <PackContext.Provider value={pack}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <div className="container site-header-inner">
          <Link to="/" className="wordmark">
            Aletheia
          </Link>
          <nav aria-label="Main">
            <ul className="nav-list">
              <li>
                <NavLink to="/" end>
                  Home
                </NavLink>
              </li>
              <li>
                <NavLink to="/catalog">Catalog</NavLink>
              </li>
              <li>
                <NavLink to="/outcomes">By outcome</NavLink>
              </li>
              <li>
                <NavLink to="/build/new">New course</NavLink>
              </li>
              <li>
                <NavLink to="/programs/new">For programs</NavLink>
              </li>
              <li>
                <NavLink to="/about">About</NavLink>
              </li>
              <li>
                <NavLink to="/feedback" state={{ from: location.pathname }}>
                  Provide feedback
                </NavLink>
              </li>
            </ul>
          </nav>
        </div>
      </header>
      <main id="main" ref={mainRef} tabIndex={-1} className="site-main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/focus/:focus" element={<FocusPage />} />
          <Route path="/arcs/:id" element={<ArcDetail />} />
          <Route path="/build/:id" element={<Builder />} />
          <Route path="/start" element={<StartCourse />} />
          <Route path="/feedback" element={<Feedback />} />
          <Route path="/programs/new" element={<NewProgram />} />
          <Route path="/programs/:id" element={<ProgramPage />} />
          <Route path="/programs/:id/admin" element={<ProgramAdmin />} />
          <Route path="/catalog" element={<Catalog />} />
          <Route path="/outcomes" element={<Outcomes />} />
          <Route path="/about" element={<About />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="site-footer">
        <div className="container">
          <p>
            Aletheia builds free course readers from openly licensed work. Readers are never sold. Catalog records are
            shared under CC BY 4.0; each reading keeps its own license.
          </p>
        </div>
      </footer>
    </PackContext.Provider>
  );
}
