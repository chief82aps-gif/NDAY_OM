export default function Terms() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-ndl-blue to-blue-700 px-4 py-12">
      <div className="w-full max-w-2xl mx-auto">
        <div className="text-center mb-8">
          <div className="inline-block w-16 h-16 bg-white rounded-lg flex items-center justify-center mb-4">
            <span className="text-ndl-blue font-bold text-4xl">N</span>
          </div>
          <h1 className="text-3xl font-bold text-white mb-2">Terms of Service</h1>
          <p className="text-blue-100">NDAY Route Manager — New Day Logistics LLC</p>
        </div>

        <div className="bg-white rounded-lg shadow-2xl p-8 space-y-5 text-gray-700">
          <p className="text-sm text-gray-500">Last updated: July 2026</p>

          <p>
            NDAY Route Manager is a private, internal operations platform owned and
            operated by New Day Logistics LLC ("New Day"). It is provided solely for
            use by New Day employees and contractors in connection with their work
            for New Day, and is not available for public registration or use.
          </p>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">Access</h2>
            <p>
              Accounts are issued and managed by New Day administrators. Access may be
              suspended or revoked at any time, including upon termination of a
              user's employment or contractor relationship with New Day.
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">Acceptable use</h2>
            <p>
              The platform may only be used for legitimate New Day business purposes,
              in accordance with New Day's internal policies. Users may not attempt to
              access data or functionality outside their authorized role.
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">No warranty</h2>
            <p>
              The platform is provided "as is," for internal operational use, without
              warranties of any kind. New Day reserves the right to modify, suspend,
              or discontinue any part of the platform at any time.
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-gray-900 mb-1">Contact</h2>
            <p>
              Questions about these terms can be directed to{' '}
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
