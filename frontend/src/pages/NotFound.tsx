import { Link } from "react-router-dom";
import { useDocumentTitle } from "../hooks";

export default function NotFound() {
  useDocumentTitle("Page not found");
  return (
    <div className="container page narrow">
      <h1>Page not found</h1>
      <p className="prose">There's nothing at this address. The link may be mistyped, or the page may have moved.</p>
      <p>
        <Link to="/">Go to the home page</Link>
      </p>
    </div>
  );
}
