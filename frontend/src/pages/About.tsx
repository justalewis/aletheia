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

      <p className="prose">
        I vibe-coded this web application based on my years of teaching in the field, keying the work of the assembler to
        my knowledge of the shapes writing classes often take (e.g., FYC, technical communication, research writing,
        etc.). The corpus that composes the catalogue favors open textbooks and collections that instructors in Writing
        Studies already use but haven't been collated into a single location (afaik). No company, publisher, or vendor
        sponsors this work and no money changes hands for your use. I just pay for the hosting of the web app on a fly.io
        deployment. Nothing on this site is for sale. Ever.
      </p>

      <h2>How it was built</h2>
      <p className="prose">
        I designed Aletheia and decided what belongs in it: which sources count, how the reuse labels work, and what the
        site will and will not do. In building it I worked with Claude, an AI model from Anthropic, as a coding
        assistant. Claude wrote much of the software and drafted the catalog's sample-course descriptions under my
        direction. Once you are using the site, no AI model is involved. Matching readings to outcomes follows fixed
        rules over the catalog's own topic tags, so the same request returns the same result.
      </p>

      <h2>Use it as guidance</h2>
      <p className="prose">
        Full disclosure: sometimes the match between a reading and an outcome or an outcome and assignment might miss.
        This is because the course plans and reading lists Aletheia produces are starting points for an instructor's own
        judgment about what students read, in what order, and why. Because outcome wording varies from one institution
        to the next, please check any outcome against your institution's official text before relying on it.
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
        Aletheia will never be in the business of selling course readers and it hopes you won't either. Obviously, a
        campus copy center can print a reader and charge for the price of production, which noncommercial licenses allow.
        But any charging above that printing cost is a breach of the open licensing the readings and assignments included
        herein were given by their authors. Your own selection and arrangement can be shared under CC BY 4.0. The code
        is released under the MIT license and the catalog records under CC BY 4.0, and both are on GitHub. Corrections,
        questions, and reports of broken links are welcome through the <Link to="/feedback">feedback form</Link>.
      </p>
      <p>
        <Link to="/catalog">Browse the catalog</Link>
      </p>
    </div>
  );
}
