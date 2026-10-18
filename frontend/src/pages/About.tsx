import { Link } from "react-router-dom";
import { TierBadge } from "../components/TierBadge";
import { useDocumentTitle } from "../hooks";

export default function About() {
  useDocumentTitle("About");
  return (
    <div className="container page narrow">
      <h1>About Aletheia</h1>
      <p className="prose">
        Aletheia helps writing instructors put together a course reader from openly licensed readings and assignments.
        You pick a sample course or start blank, fit it to your term, and export a reader and an LMS package. The reader
        credits every item with its title, author, source, and license.
      </p>

      <h2>What the reuse labels mean</h2>
      <p className="prose">Every reading and assignment in the catalog has one of four labels, based on its license.</p>
      <dl className="tier-list">
        <div>
          <dt>
            <TierBadge tier="adapt" />
          </dt>
          <dd>CC BY, CC BY-SA, CC0, or public domain. You can copy it, edit it, and remix it, as long as you credit it.</dd>
        </div>
        <div>
          <dt>
            <TierBadge tier="adapt-nc" />
          </dt>
          <dd>
            CC BY-NC or CC BY-NC-SA. Same as above, but only for noncommercial use. A free reader qualifies; a reader sold
            for profit doesn't.
          </dd>
        </div>
        <div>
          <dt>
            <TierBadge tier="verbatim" />
          </dt>
          <dd>NoDerivatives licenses. You can include it whole and unchanged, but not excerpt or edit it.</dd>
        </div>
        <div>
          <dt>
            <TierBadge tier="link-only" />
          </dt>
          <dd>
            Free to read online, but all rights reserved. The reader links to it instead of copying it, and students follow
            the link.
          </dd>
        </div>
      </dl>
      <p className="prose">
        If we haven't verified an item's license yet, Aletheia treats it as link only, whatever the source says, until
        someone checks it.
      </p>

      <h2>Free, and never sold</h2>
      <p className="prose">
        Aletheia never sells readers, and it doesn't ask you to. A campus copy center can print a reader and charge what
        printing costs, which noncommercial licenses allow. Your own selection and arrangement can be shared under CC BY
        4.0.
      </p>
      <p>
        <Link to="/catalog">Browse the catalog</Link>
      </p>
    </div>
  );
}
