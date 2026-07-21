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
