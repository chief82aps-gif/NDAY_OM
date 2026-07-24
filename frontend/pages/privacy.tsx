export default function Privacy() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-ndl-blue to-blue-700 px-4 py-12">
      <div className="w-full max-w-2xl mx-auto">
        <div className="text-center mb-8">
          <div className="inline-block w-16 h-16 bg-white rounded-lg flex items-center justify-center mb-4">
            <span className="text-ndl-blue font-bold text-4xl">N</span>
          </div>
          <h1 className="text-3xl font-bold text-white mb-2">Privacy Policy</h1>
          <p className="text-blue-100">NDAY Route Manager — New Day Logistics LLC</p>
        </div>

        <div className="bg-white rounded-lg shadow-2xl p-8 space-y-5 text-gray-700">
          <p className="text-sm text-gray-500">Last updated: July 2026</p>

          <p>
            NDAY Route Manager ("the platform") is an internal operations tool built
            and operated by New Day Logistics LLC ("New Day," "we," "us"), an Amazon
            Delivery Service Partner. It is used only by New Day employees and
            contractors in the course of their work, and is not offered to the general
            public.
          </p>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">What we collect</h2>
            <p>
              The platform processes operational data needed to run a delivery
              business, including: driver names, schedules, route and van
              assignments, vehicle inspection records, attendance and performance
              data, and invoice/financial reconciliation data supplied by Amazon.
            </p>
            <p className="mt-2">
              Separately, for hiring purposes, the platform processes job candidate
              information sourced from Indeed — name, phone number, email address,
              resume/work history, and screening answers — for candidates who apply
              to New Day Logistics driving positions.
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">How we use it</h2>
            <p>
              Operational data is used solely to run day-to-day dispatch, scheduling,
              safety compliance, and payroll processes. Candidate data is used solely
              to manage our hiring pipeline — creating and updating candidate records
              in our Asana hiring board and, when contact information is available,
              creating a corresponding contact in a company-managed Google account
              used by our recruiting team.
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">Who can access it</h2>
            <p>
              Access to the platform requires an authorized login issued by a New Day
              administrator. Data is not sold, rented, or shared with third parties
              for advertising or marketing purposes. We use established third-party
              business tools (including Google Workspace/People API, Asana, and
              Slack) strictly as internal processing tools under our own account —
              these providers do not receive rights to use the data beyond providing
              their service to us.
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">How we protect it</h2>
            <p>
              We treat candidate contact information and any other personal data as
              sensitive and protect it with the following measures:
            </p>
            <ul className="mt-2 list-disc list-inside space-y-1">
              <li>
                <strong>Encryption in transit:</strong> all data exchanged between the
                platform, its users, and integrated services (Google People API, Asana)
                is transmitted over HTTPS/TLS.
              </li>
              <li>
                <strong>Encryption at rest:</strong> candidate contact information
                synced through the platform is stored in our company-managed Google
                (Contacts) and Asana accounts, which encrypt data at rest and apply
                their own security controls. The platform's own operational database is
                kept in a secured, access-restricted hosting environment.
              </li>
              <li>
                <strong>Access controls:</strong> access requires an individually
                issued, authenticated login, and is restricted to authorized New Day
                staff on a least-privilege basis. OAuth tokens for connected Google
                accounts are stored as protected server-side secrets and are never
                exposed to the browser or to end users.
              </li>
              <li>
                <strong>Limited scope:</strong> we request only the Google API scopes
                required to create and update recruiting contacts, and use them only
                for that purpose.
              </li>
            </ul>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">Google user data — Limited Use</h2>
            <p>
              The platform's use and transfer of information received from Google APIs
              adheres to the{' '}
              <a
                href="https://developers.google.com/terms/api-services-user-data-policy"
                className="text-ndl-blue underline"
                target="_blank"
                rel="noopener noreferrer"
              >
                Google API Services User Data Policy
              </a>
              , including the Limited Use requirements. We do not use Google user data
              for advertising, do not sell it, and do not transfer or share it with
              third parties except as necessary to provide the recruiting feature
              described above, to comply with applicable law, or as part of a merger or
              acquisition.
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">Data retention</h2>
            <p>
              Data is retained for as long as reasonably necessary for the business
              and legal purposes described above, and is deleted or archived at our
              discretion when no longer needed.
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">Contact</h2>
            <p>
              Questions about this policy or your data can be directed to{' '}
              <a href="mailto:jaysonwatson@newdaylogisticsllc.com" className="text-ndl-blue underline">
                jaysonwatson@newdaylogisticsllc.com
              </a>
              .
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
